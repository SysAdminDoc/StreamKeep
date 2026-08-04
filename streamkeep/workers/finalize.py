"""Background finalization for completed downloads."""

import os
import re
import hashlib
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from ..extractors import TwitchExtractor
from ..metadata import MetadataSaver
from ..postprocess import PostProcessor
from ..postprocess.processor import PP_LOCK as _PP_LOCK
from ..utils import OutputPathError, fmt_size, validate_output_path
from ..upgrade import UpgradePaths, activate_upgrade_version
from ..verify import (
    STATUS_OK,
    create_archive_manifest,
    verify_archive_manifest,
    verify_recording_dir,
    write_archive_manifest_sidecar,
)


class FinalizeWorker(QThread):
    """Runs metadata/chat/post-processing off the UI thread."""

    log = pyqtSignal(str)
    progress = pyqtSignal(str, int, int)
    done = pyqtSignal(dict)

    def __init__(self, task):
        super().__init__()
        self.task = dict(task or {})
        self._cancel = False
        self._podcast_feed_body = ""

    def cancel(self):
        self._cancel = True
        self.requestInterruption()

    def _interrupted(self):
        return self._cancel or self.isInterruptionRequested()

    def _has_postprocess_work(self, snapshot):
        if not snapshot:
            return False
        flags = (
            "extract_audio",
            "normalize_loudness",
            "reencode_h265",
            "contact_sheet",
            "split_by_chapter",
            "convert_video",
            "convert_audio",
        )
        return any(bool(snapshot.get(name)) for name in flags)

    def _chat_vod_id(self, info):
        if getattr(info, "platform", "") != "Twitch":
            return ""
        url = getattr(info, "url", "") or ""
        match = re.search(r"/vod/(\d+)\.m3u8", url)
        return match.group(1) if match else ""

    def _output_size_label(self, out_dir):
        if not out_dir or not os.path.isdir(out_dir):
            return ""
        total = 0
        try:
            for root, _dirs, files in os.walk(out_dir):
                for name in files:
                    path = os.path.join(root, name)
                    try:
                        total += os.path.getsize(path)
                    except OSError:
                        continue
        except OSError:
            return ""
        return fmt_size(total) if total > 0 else ""

    def _podcast_feed_url(self, task, info):
        """Return the originating RSS feed for a podcast episode, or ''."""
        platform = str(getattr(info, "platform", "") or task.get("platform", ""))
        if platform.lower() != "podcast":
            return ""
        return str(task.get("feed_url", "") or getattr(info, "feed_url", "") or "")

    def _enrich_podcast_info(self, task, info):
        """Refresh the episode's public RSS metadata before saving metadata.json."""
        feed_url = self._podcast_feed_url(task, info)
        enclosure = str(getattr(info, "url", "") or task.get("history_url", "") or "")
        if not feed_url or not enclosure or getattr(info, "podcast_metadata", None):
            return
        try:
            from ..image_fetch import fetch_url_bytes
            feed_bytes = fetch_url_bytes(
                feed_url, max_bytes=16 * 1024 * 1024,
                accept="application/rss+xml, application/xml, text/xml, */*",
            )
            body = feed_bytes.decode("utf-8", errors="replace")
            from ..extractors.podcast import find_podcast_episode
            episode = find_podcast_episode(body, enclosure, feed_url=feed_url)
        except Exception as error:
            self.log.emit(f"[PODCAST] Metadata refresh skipped: {error}")
            return
        self._podcast_feed_body = body
        if not episode:
            return
        metadata = episode.get("metadata") or {}
        if metadata:
            info.podcast_metadata = metadata
        if episode.get("artwork_url") and not getattr(info, "thumbnail_url", ""):
            info.thumbnail_url = episode["artwork_url"]
        if not getattr(info, "source_id", ""):
            identity = str(
                episode.get("guid") or episode.get("enclosure", {}).get("url") or ""
            )
            if identity:
                info.source_id = "episode:" + hashlib.sha256(
                    identity.encode("utf-8", errors="replace")
                ).hexdigest()

    def _podcast_integrity_media_path(self, out_dir):
        media_suffixes = (
            ".mp4", ".mkv", ".ts", ".webm", ".mov", ".avi", ".m4v",
            ".mp3", ".m4a", ".ogg", ".opus", ".flac", ".wav", ".aac",
        )
        try:
            for name in sorted(os.listdir(out_dir or "")):
                if name.lower().endswith(media_suffixes) and not name.startswith("."):
                    candidate = os.path.join(out_dir, name)
                    if os.path.isfile(candidate):
                        return candidate
        except OSError:
            pass
        return ""

    def _run_podcast_integrity(self, task, info, out_dir):
        metadata = getattr(info, "podcast_metadata", None) or {}
        alternates = metadata.get("alternate_enclosures", [])
        if not isinstance(alternates, list) or not any(
            isinstance(row, dict) and isinstance(row.get("integrity"), dict)
            for row in alternates
        ):
            return ""
        try:
            from ..podcast_sidecars import verify_podcast_integrity
            urls = [
                getattr(info, "url", ""),
                task.get("history_url", ""),
                task.get("vod_source", ""),
            ]
            verification = verify_podcast_integrity(
                self._podcast_integrity_media_path(out_dir),
                alternates,
                downloaded_urls=urls,
            )
            MetadataSaver.update_podcast_integrity(out_dir, verification)
        except Exception as error:
            self.log.emit(f"[PODCAST] Integrity verification failed: {error}")
            return f"Podcast integrity verification failed: {error}"
        failures = [
            row for row in verification
            if row.get("status") in {
                "mismatch", "invalid", "unsupported", "media_missing",
                "media_unreadable",
            }
        ]
        for row in failures:
            self.log.emit(
                "[PODCAST] Publisher integrity not accepted: "
                f"{row.get('status', 'unknown')} for {row.get('source', '')}"
            )
        if failures:
            return "Podcast publisher integrity verification did not pass"
        if any(row.get("status") == "verified" for row in verification):
            self.log.emit("[PODCAST] Publisher integrity verified")
        return ""

    def _planned_steps(self, task, info, snapshot):
        steps = [("Saving metadata", "metadata")]
        if task.get("write_nfo"):
            steps.append(("Writing NFO", "nfo"))
        if getattr(info, "chapters", None):
            steps.append(("Exporting chapters", "chapters"))
        if getattr(info, "markers", None) or getattr(info, "marker_schedules", None):
            steps.append(("Exporting HLS markers", "markers"))
        if task.get("download_chat") and self._chat_vod_id(info):
            steps.append(("Downloading chat", "chat"))
        if self._podcast_feed_url(task, info):
            steps.append(("Fetching podcast sidecars", "sidecars"))
        podcast_metadata = getattr(info, "podcast_metadata", None) or {}
        if any(
            isinstance(row, dict) and isinstance(row.get("integrity"), dict)
            for row in podcast_metadata.get("alternate_enclosures", [])
        ):
            steps.append(("Verifying podcast integrity", "podcast_integrity"))
        if self._music_tag_targets(task, info):
            steps.append(("Filling music tags", "music_tags"))
        if self._has_postprocess_work(snapshot):
            steps.append(("Running post-processing", "postprocess"))
        if task.get("record_manifest", True):
            steps.append(("Capturing integrity manifest", "manifest"))
        if task.get("is_upgrade") and task.get("record_manifest", True):
            steps.append(("Activating verified upgrade", "activate"))
        return steps

    def _run_podcast_sidecars(self, task, info, out_dir, file_base):
        """Fetch transcript/chapter sidecars for a podcast episode from its
        originating feed, writing them next to the recording. Best-effort:
        a missing feed, network error, or empty result is non-fatal."""
        feed_url = self._podcast_feed_url(task, info)
        enclosure = str(getattr(info, "url", "") or "")
        if not feed_url or not enclosure or not out_dir:
            return
        try:
            from ..image_fetch import fetch_url_bytes
            from ..podcast_sidecars import sync_podcast_sidecars
            feed_body = self.__dict__.get("_podcast_feed_body", "")
            if not feed_body:
                feed_bytes = fetch_url_bytes(
                    feed_url, max_bytes=16 * 1024 * 1024,
                    accept="application/rss+xml, application/xml, text/xml, */*",
                )
                feed_body = feed_bytes.decode("utf-8", errors="replace")
        except Exception as e:
            self.log.emit(f"[SIDECARS] Could not fetch feed: {e}")
            return
        try:
            manifest = sync_podcast_sidecars(
                feed_body,
                enclosure, out_dir, file_base or "episode",
                log_fn=self.log.emit,
            )
        except Exception as e:
            self.log.emit(f"[SIDECARS] Sidecar sync failed: {e}")
            return
        if manifest:
            kinds = ", ".join(sorted({entry.get("kind", "?") for entry in manifest}))
            self.log.emit(f"[SIDECARS] Wrote {len(manifest)} sidecar(s): {kinds}")

    def _music_tag_targets(self, task, info):
        """Return audio outputs whose album-artist should be filled (V41)."""
        platform = str(
            getattr(info, "platform", "") or task.get("platform", "")
        )
        from ..postprocess.music_tags import find_audio_outputs, is_music_platform
        if not is_music_platform(platform):
            return []
        out_dir = task.get("out_dir", "") or task.get("output_dir", "")
        if not out_dir:
            return []
        return find_audio_outputs(out_dir)

    def _run_music_tags(self, task, info, targets):
        """Fill missing album-artist tags without overwriting existing ones."""
        from ..capabilities import CapabilityUnavailableError, resolve_tool_command
        from ..postprocess.music_tags import apply_music_tags

        try:
            ffmpeg = resolve_tool_command("ffmpeg")
            ffprobe = resolve_tool_command("ffprobe")
        except CapabilityUnavailableError as error:
            self.log.emit(f"[TAGS] {error}")
            return
        channel = str(getattr(info, "channel", "") or task.get("channel", ""))
        album = str(task.get("album", "") or getattr(info, "album", "") or "")
        if not album and str(
            getattr(info, "platform", "") or ""
        ).lower() == "podcast":
            # A podcast show groups naturally as an album; a music track does
            # not, so only this case derives one.
            album = channel
        for target in targets:
            ok, applied, message = apply_music_tags(
                target, channel=channel, album=album,
                title=str(getattr(info, "title", "") or ""),
                ffmpeg=ffmpeg, ffprobe=ffprobe,
            )
            if applied:
                self.log.emit(
                    f"[TAGS] {Path(target).name}: {message}"
                )
            elif not ok:
                self.log.emit(f"[TAGS] {Path(target).name}: {message}")

    def _emit_progress(self, label, index, total):
        self.progress.emit(label, index, total)

    def run(self):
        task = dict(self.task)
        info = task.get("info")
        out_dir = task.get("out_dir", "")
        file_base = task.get("file_base", "")
        snapshot = dict(task.get("postprocess_snapshot") or {})
        chat_vod_id = self._chat_vod_id(info) if info else ""
        result = {
            "platform": task.get("platform", "?"),
            "title": task.get("title", "?"),
            "channel": task.get("channel", ""),
            "quality_name": task.get("quality_name", ""),
            "out_dir": out_dir,
            "history_url": task.get("history_url", ""),
            "source_id": task.get(
                "source_id", getattr(info, "source_id", "") if info else "",
            ),
            "webpage_url": task.get(
                "webpage_url", getattr(info, "webpage_url", "") if info else "",
            ),
            "queue_job_id": task.get("queue_job_id", ""),
            "is_upgrade": bool(task.get("is_upgrade", False)),
            "upgrade_history_id": int(task.get("upgrade_history_id", 0) or 0),
            "upgrade_decision_id": int(task.get("upgrade_decision_id", 0) or 0),
            "upgrade_activated": False,
            "info": info,
            "cancelled": False,
            "finalize_error": "",
            "archive_manifest": None,
            "archive_manifest_error": "",
        }
        step_no = 0
        total_steps = 0
        if self._interrupted():
            result["cancelled"] = True
            self.done.emit(result)
            return
        if out_dir:
            try:
                validate_output_path(out_dir, file_base=file_base)
            except OutputPathError as error:
                result["finalize_error"] = str(error)
                self.log.emit(f"[PREFLIGHT] {error}")
                self.done.emit(result)
                return

        # Only snapshot keys that actually exist on PostProcessor — a stale
        # config key must not AttributeError-crash the entire finalize pass
        # before metadata is even saved.
        # Guard PostProcessor class-level state with a lock so concurrent
        # FinalizeWorker threads don't clobber each other.
        _PP_LOCK.acquire()
        orig = {k: getattr(PostProcessor, k) for k in snapshot if hasattr(PostProcessor, k)}
        try:
            if info and out_dir:
                self._enrich_podcast_info(task, info)
                result["source_id"] = str(
                    getattr(info, "source_id", "") or result.get("source_id", "")
                )
                result["webpage_url"] = str(
                    getattr(info, "webpage_url", "") or result.get("webpage_url", "")
                )
                steps = self._planned_steps(task, info, snapshot)
                total_steps = len(steps)

                step_no += 1
                self._emit_progress("Saving metadata", step_no, total_steps)
                saved = MetadataSaver.save(
                    out_dir,
                    info,
                    source_url=task.get("history_url", ""),
                )
                if (
                    getattr(info, "thumbnail_url", "")
                    and not saved.get("thumbnail_path")
                ):
                    self.log.emit(
                        "[METADATA] Remote thumbnail could not be saved; "
                        "public sidecars contain no remote fallback URL."
                    )
                if self._interrupted():
                    result["cancelled"] = True
                    self.done.emit(result)
                    return
                if task.get("write_nfo"):
                    step_no += 1
                    self._emit_progress("Writing NFO", step_no, total_steps)
                    MetadataSaver.write_nfo(
                        out_dir,
                        info,
                        file_base=file_base,
                        source_url=task.get("history_url", ""),
                    )
                    self.log.emit(f"[NFO] Wrote {file_base or 'movie'}.nfo for media library")
                if getattr(info, "chapters", None):
                    step_no += 1
                    self._emit_progress("Exporting chapters", step_no, total_steps)
                    if MetadataSaver.write_chapters(out_dir, info, file_base=file_base):
                        count = len(getattr(info, "chapters", None) or [])
                        self.log.emit(f"[CHAPTERS] Exported {count} chapter(s) to {file_base}.chapters.txt/.json")
                if (
                    getattr(info, "markers", None)
                    or getattr(info, "marker_schedules", None)
                ) and not self._interrupted():
                    step_no += 1
                    self._emit_progress("Exporting HLS markers", step_no, total_steps)
                    if MetadataSaver.write_hls_markers(
                        out_dir,
                        getattr(info, "markers", None),
                        schedules=getattr(info, "marker_schedules", None),
                        file_base=file_base,
                    ):
                        self.log.emit(
                            f"[HLS] Exported marker sidecar for "
                            f"{file_base or 'recording'}"
                        )
                if task.get("download_chat") and chat_vod_id and not self._interrupted():
                    step_no += 1
                    self._emit_progress("Downloading chat", step_no, total_steps)
                    vod_id = chat_vod_id
                    if vod_id:
                        chat_base = os.path.join(out_dir, file_base or "chat")
                        self.log.emit(f"[CHAT] Fetching chat replay for VOD {vod_id}...")
                        count, err = TwitchExtractor().download_chat(
                            vod_id, chat_base, log_fn=self.log.emit
                        )
                        if err:
                            self.log.emit(f"[CHAT] Failed: {err}")
                        else:
                            self.log.emit(f"[CHAT] Saved {count} comments to {file_base or 'chat'}.chat.json/.txt")
                if not self._interrupted():
                    # Flatten any yt-dlp YouTube live-chat replay that was
                    # downloaded alongside the media into the shared chat model.
                    try:
                        from ..chat.youtube_replay import ingest_replay_dir
                        ingest_replay_dir(out_dir, log_fn=self.log.emit)
                    except Exception as e:
                        self.log.emit(f"[CHAT] Replay normalization skipped: {e}")
                if self._podcast_feed_url(task, info) and not self._interrupted():
                    step_no += 1
                    self._emit_progress("Fetching podcast sidecars", step_no, total_steps)
                    self._run_podcast_sidecars(task, info, out_dir, file_base)
                if not self._interrupted():
                    integrity_error = self._run_podcast_integrity(task, info, out_dir)
                    if integrity_error:
                        result["podcast_integrity_error"] = integrity_error
                        result["finalize_error"] = integrity_error
                music_targets = self._music_tag_targets(task, info)
                if music_targets and not self._interrupted():
                    step_no += 1
                    self._emit_progress("Filling music tags", step_no, total_steps)
                    self._run_music_tags(task, info, music_targets)
                if self._has_postprocess_work(snapshot) and not self._interrupted():
                    step_no += 1
                    self._emit_progress("Running post-processing", step_no, total_steps)
                if snapshot and not self._interrupted():
                    for k, v in snapshot.items():
                        if hasattr(PostProcessor, k):
                            setattr(PostProcessor, k, v)
                    if PostProcessor.has_any_preset():
                        PostProcessor.process_directory(
                            out_dir,
                            log_fn=self.log.emit,
                            chapters=getattr(info, "chapters", None) or None,
                        )
        except Exception as e:
            result["finalize_error"] = str(e)
            self.log.emit(f"[FINALIZE] Background finalization error: {e}")
        finally:
            for k, v in orig.items():
                setattr(PostProcessor, k, v)
            _PP_LOCK.release()

        if (
            out_dir
            and not self._interrupted()
            and not result["finalize_error"]
            and task.get("record_manifest", True)
        ):
            try:
                step_no += 1
                self._emit_progress(
                    "Capturing integrity manifest",
                    step_no,
                    total_steps,
                )
                if task.get("is_upgrade"):
                    status, details, _media_path = verify_recording_dir(
                        out_dir,
                        float(task.get("expected_duration", 0) or 0),
                    )
                    if status != STATUS_OK:
                        raise RuntimeError(
                            f"Upgrade media verification did not pass: {details}"
                        )
                    self.log.emit(f"[UPGRADE] Media verified: {details}")
                manifest = create_archive_manifest(
                    out_dir,
                    write_sidecar=not bool(task.get("is_upgrade")),
                )
                if task.get("is_upgrade"):
                    status, details, _report = verify_archive_manifest(
                        out_dir, manifest,
                    )
                    if status != STATUS_OK:
                        raise RuntimeError(
                            "Upgrade integrity verification did not pass: "
                            f"{details}"
                        )
                    if self._interrupted():
                        result["cancelled"] = True
                        self.done.emit(result)
                        return
                    paths = UpgradePaths(
                        existing=Path(task.get("upgrade_existing_path", "")).resolve(
                            strict=False
                        ),
                        staging=Path(out_dir).resolve(strict=False),
                        final=Path(task.get("upgrade_final_dir", "")).resolve(
                            strict=False
                        ),
                    )
                    manifest["root"] = str(paths.final)
                    write_archive_manifest_sidecar(out_dir, manifest)
                    step_no += 1
                    self._emit_progress(
                        "Activating verified upgrade",
                        step_no,
                        total_steps,
                    )
                    final_dir = activate_upgrade_version(
                        paths,
                        version_keep=int(task.get("upgrade_version_keep", 3) or 3),
                        log_fn=self.log.emit,
                    )
                    out_dir = str(final_dir)
                    result["out_dir"] = out_dir
                    result["upgrade_previous_path"] = str(paths.existing)
                    result["upgrade_activated"] = True
                    if result["upgrade_decision_id"]:
                        try:
                            from .. import db as _db
                            _db.update_upgrade_decision(
                                result["upgrade_decision_id"],
                                execution_status="activated",
                                activation_path=out_dir,
                                previous_path=str(paths.existing),
                            )
                        except Exception as error:
                            self.log.emit(
                                f"[UPGRADE] Decision status could not be updated: {error}"
                            )
                    self.log.emit(
                        f"[UPGRADE] Activated verified version: {out_dir}"
                    )
                result["archive_manifest"] = manifest
                self.log.emit(
                    f"[VERIFY] Integrity manifest captured for "
                    f"{len(manifest.get('files', []) or [])} file(s)."
                )
            except Exception as e:
                result["archive_manifest_error"] = str(e)
                if result.get("upgrade_decision_id"):
                    try:
                        from .. import db as _db
                        _db.update_upgrade_decision(
                            result["upgrade_decision_id"],
                            execution_status="failed",
                            execution_error=str(e),
                        )
                    except Exception:
                        pass  # safe: best-effort fallback; preserve the primary operation
                self.log.emit(f"[VERIFY] Could not capture integrity manifest: {e}")

        result["cancelled"] = self._interrupted()
        result["size_label"] = self._output_size_label(out_dir) if not result["cancelled"] else ""
        self.done.emit(result)
