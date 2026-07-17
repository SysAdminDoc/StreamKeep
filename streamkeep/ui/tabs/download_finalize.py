"""Finalize, history, duplicate, and metadata handlers for Download."""

import copy
import os
import urllib.parse

from ... import db as _db
from ...extractors import Extractor, TwitchExtractor
from ...utils import safe_filename as _safe_filename
from ...workers import FinalizeWorker


class DownloadFinalizeMixin:
    """Serialized finalization and completed-recording persistence."""

    def _start_finalize_worker(self):
        worker = getattr(self, "_finalize_worker", None)
        if worker is not None and worker.isRunning():
            return
        if not self._finalize_tasks:
            self._finalize_active_title = ""
            self._finalize_active_label = ""
            self._finalize_active_step = 0
            self._finalize_active_total = 0
            self._refresh_download_summary()
            return
        task = self._finalize_tasks.pop(0)
        self._finalize_active_title = task.get("title", "")
        self._finalize_active_label = "Preparing background cleanup"
        self._finalize_active_step = 0
        self._finalize_active_total = 0
        worker = FinalizeWorker(task)
        worker.log.connect(self._log)
        worker.progress.connect(self._on_finalize_progress)
        worker.done.connect(self._on_finalize_done)
        self._finalize_worker = worker
        worker.start()
        self._refresh_download_summary()
        if not self._foreground_busy():
            extra = f" {len(self._finalize_tasks)} more queued." if self._finalize_tasks else ""
            self._set_status(
                f"Finalizing {task.get('title', 'download')[:60]} in the background.{extra}",
                "processing",
            )

    def _enqueue_finalize_task(self, task):
        self._finalize_tasks.append(task)
        if len(self._finalize_tasks) > 1 or (
                self._finalize_worker is not None and self._finalize_worker.isRunning()):
            self._log(f"[FINALIZE] Queued background finalization for {task.get('title', 'download')[:60]}")
        self._refresh_download_summary()
        self._start_finalize_worker()

    def _on_finalize_progress(self, label, step_no, total_steps):
        self._finalize_active_label = label or ""
        self._finalize_active_step = max(0, int(step_no or 0))
        self._finalize_active_total = max(self._finalize_active_step, int(total_steps or 0))
        self._refresh_download_summary()
        if not self._foreground_busy():
            count = ""
            if self._finalize_active_total:
                count = f" ({self._finalize_active_step}/{self._finalize_active_total})"
            extra = f" {len(self._finalize_tasks)} more queued." if self._finalize_tasks else ""
            title = self._finalize_active_title[:60] or "download"
            step_text = f"{self._finalize_active_label}{count}" if self._finalize_active_label else f"step{count}"
            self._set_status(f"Finalizing {title}: {step_text}.{extra}", "processing")

    def _on_finalize_done(self, result):
        worker = getattr(self, "_finalize_worker", None)
        if worker is not None and not worker.isRunning():
            try:
                worker.wait(200)
            except Exception:
                pass
        self._finalize_worker = None
        self._finalize_active_title = ""
        self._finalize_active_label = ""
        self._finalize_active_step = 0
        self._finalize_active_total = 0
        finished_title = result.get("title", "download")
        if not result.get("cancelled"):
            history_entry = self._add_history(
                result.get("platform", "?"),
                result.get("title", "?"),
                result.get("quality_name", ""),
                result.get("size_label", self._output_size_label(result.get("out_dir", ""))),
                result.get("out_dir", ""),
                channel=result.get("channel", ""),
                url=result.get("history_url", ""),
            )
            manifest = result.get("archive_manifest")
            if history_entry is not None and manifest:
                try:
                    _db.save_archive_manifest(
                        history_entry.db_id,
                        history_entry.path,
                        manifest,
                        status="created",
                        details=(
                            f"Captured {len(manifest.get('files', []) or [])} "
                            "file(s)"
                        ),
                    )
                except Exception as e:
                    self._log(f"[VERIFY] Could not save integrity manifest: {e}")
            elif result.get("archive_manifest_error"):
                self._log(
                    "[VERIFY] Integrity manifest was not saved: "
                    f"{result.get('archive_manifest_error')}"
                )
            finalize_error = result.get("finalize_error") or result.get("archive_manifest_error")
            if finalize_error:
                self._record_failed_job(
                    stage="finalize",
                    error=finalize_error,
                    out_dir=result.get("out_dir", ""),
                    queue_data={
                        "url": result.get("history_url", ""),
                        "title": result.get("title", ""),
                        "platform": result.get("platform", "?"),
                    },
                )
        remaining = len(self._finalize_tasks)
        self._refresh_download_summary()
        if not self._foreground_busy():
            if result.get("cancelled"):
                self._set_status("Background finalization was cancelled.", "warning")
            elif remaining:
                self._set_status(
                    f"Finished finalizing {finished_title[:60]}. {remaining} background job(s) remaining.",
                    "processing",
                )
            else:
                self._set_status(
                    f"Background finalization complete for {finished_title[:60]}.",
                    "success",
                )
        self._start_finalize_worker()


    # ── History / duplicate ─────────────────────────────────────

    def _infer_history_channel(self, url="", platform="", channel=""):
        channel = (channel or "").strip()
        if channel and not channel.lower().startswith("vod_"):
            return channel
        if not url:
            return ""
        try:
            ext = Extractor.detect(url)
            if ext is not None:
                detected = (ext.extract_channel_id(url) or "").strip()
                if (
                    detected
                    and not detected.lower().startswith("vod_")
                    and not detected.isdigit()
                    and getattr(ext, "NAME", "") in {"Kick", "Twitch", "SoundCloud", "Audius", "Podcast"}
                ):
                    return detected
            parsed = urllib.parse.urlparse(url)
            parts = [p for p in parsed.path.strip("/").split("/") if p]
            blocked = {
                "videos", "video", "embed", "watch", "shorts",
                "playlist", "live", "vod", "v",
            }
            for part in parts:
                if part.lower() not in blocked and not part.isdigit():
                    return part
        except Exception:
            return ""
        return ""

    def _history_channel_label(self, entry):
        channel = self._infer_history_channel(
            url=getattr(entry, "url", ""),
            platform=getattr(entry, "platform", ""),
            channel=getattr(entry, "channel", ""),
        )
        if not channel:
            return ""
        platform = (getattr(entry, "platform", "") or "").strip()
        if platform:
            return f"{platform}/{channel}"
        return channel

    @staticmethod
    def _title_token_overlap(a, b):
        """Return the fraction of shared tokens between two titles (0..1)."""
        ta = set(a.strip().lower().split())
        tb = set(b.strip().lower().split())
        if not ta or not tb:
            return 0.0
        return len(ta & tb) / max(len(ta), len(tb))

    def _find_duplicate(self, url, title="", platform="", duration_secs=0):
        """Check history for a matching URL (exact), exact title, or fuzzy
        metadata match (channel + title token overlap >= 70%). Returns
        matching HistoryEntry or None."""
        if not self._check_duplicates:
            return None
        if url:
            for h in self._history:
                if h.url == url and h.path:
                    return h
        if title:
            norm = title.strip().lower()
            for h in self._history:
                if (platform and h.platform
                        and h.platform.strip().lower() != platform.strip().lower()):
                    continue
                if h.title and h.title.strip().lower() == norm and h.path:
                    return h
            # Fuzzy title-token overlap (F40)
            for h in self._history:
                if not h.title or not h.path:
                    continue
                if (platform and h.platform
                        and h.platform.strip().lower() != platform.strip().lower()):
                    continue
                if self._title_token_overlap(title, h.title) >= 0.70:
                    return h
        return None


    # ── Metadata / trim ─────────────────────────────────────────

    def _save_metadata(self, out_dir, quality_name="", history_url="", info=None):
        info = info or self.stream_info
        url = history_url or self._resolve_history_url()
        info_copy = copy.deepcopy(info) if info else None
        fallback_title = ""
        if out_dir:
            fallback_title = os.path.basename(out_dir.rstrip("\\/"))
        display_title = (
            (info_copy.title if info_copy else "")
            or fallback_title
            or "Download"
        )
        file_base = _safe_filename(info_copy.title) if info_copy and info_copy.title else ""
        task = {
            "out_dir": out_dir,
            "quality_name": quality_name,
            "history_url": url,
            "info": info_copy,
            "file_base": file_base,
            "write_nfo": bool(self._write_nfo and info_copy),
            "download_chat": bool(TwitchExtractor.download_chat_enabled and info_copy),
            "postprocess_snapshot": self._postprocess_snapshot() if info_copy else {},
            "platform": (info_copy.platform if info_copy and info_copy.platform else "?"),
            "channel": self._infer_history_channel(
                url=url,
                platform=(info_copy.platform if info_copy and info_copy.platform else "?"),
                channel=(info_copy.channel if info_copy else ""),
            ),
            "title": display_title,
        }
        self._enqueue_finalize_task(task)
        # Auto-tag recording (F35)
        try:
            from ...tags import _connect, auto_tag_recording
            db = _connect()
            auto_tag_recording(db, out_dir, info=info_copy)
            db.close()
        except Exception:
            pass
        # Index transcripts for this recording (F27)
        try:
            from ...search import index_recording
            index_recording(out_dir)
        except Exception:
            pass
