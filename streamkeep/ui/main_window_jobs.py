"""Resume discovery and serialized background-job orchestration."""

import os

from ..extractors import Extractor, YtDlpExtractor
from ..resume import (
    clear_resume_state,
    remaining_segments,
    save_resume_state,
    scan_for_orphan_sidecars,
)
from ..utils import default_output_dir as _default_output_dir
from ..workers import DownloadWorker


class MainWindowJobsMixin:
    """Recoverable transfers and bounded background work for StreamKeep."""

    def _collect_resume_scan_roots(self):
        """Directories worth scanning for orphan resume sidecars.

        Includes the active output-field value, the persisted default in
        config, and any per-channel monitor override dirs.
        """
        roots = []
        seen = set()

        def _push(path):
            if not path:
                return
            real = os.path.realpath(path)
            if real in seen:
                return
            seen.add(real)
            roots.append(path)

        try:
            _push(self.output_input.text().strip())
        except Exception:
            pass
        _push(self._config.get("output_dir", ""))
        _push(str(_default_output_dir()))
        for entry in getattr(self.monitor, "entries", []) or []:
            _push(getattr(entry, "override_output_dir", "") or "")
        return roots

    def _scan_for_resumable_downloads(self):
        """Called once at startup — shows the resume banner if any orphan
        sidecars look resumable."""
        try:
            roots = self._collect_resume_scan_roots()
            found = scan_for_orphan_sidecars(roots)
        except Exception as e:
            self._log(f"[RESUME] Scan failed: {e}")
            return
        self._resume_candidates = found
        self._refresh_resume_banner()

    def _refresh_resume_banner(self):
        """Show/hide the resume banner based on candidate count."""
        banner = getattr(self, "resume_banner", None)
        if banner is None:
            return
        count = len(getattr(self, "_resume_candidates", []) or [])
        if count <= 0:
            banner.setVisible(False)
            return
        label = getattr(self, "resume_banner_label", None)
        if label is not None:
            if count == 1:
                state = self._resume_candidates[0]
                total = len(state.segments or [])
                done = len(state.completed or [])
                title = (state.title or os.path.basename(state.output_dir) or "download")[:80]
                if total:
                    progress = f" ({done}/{total} segments done)"
                else:
                    progress = ""
                label.setText(f"Interrupted download ready to resume: {title}{progress}")
            else:
                label.setText(f"{count} interrupted downloads are ready to resume.")
        banner.setVisible(True)

    def _on_resume_all(self):
        """Resume the first candidate immediately; leave the rest queued for
        after it finishes so we don't try to run N downloads in parallel."""
        if not getattr(self, "_resume_candidates", None):
            self._refresh_resume_banner()
            return
        if self.download_worker is not None and self.download_worker.isRunning():
            self._set_status(
                "Finish or stop the active download before resuming.",
                "warning",
            )
            return
        state = self._resume_candidates[0]
        self._kick_off_resume(state)

    def _on_resume_discard(self):
        """Drop all resume candidates — remove their sidecars from disk."""
        count = 0
        for state in (getattr(self, "_resume_candidates", None) or []):
            try:
                clear_resume_state(state.output_dir)
                count += 1
            except Exception:
                pass
        self._resume_candidates = []
        self._refresh_resume_banner()
        if count:
            self._log(f"[RESUME] Discarded {count} pending resume sidecar(s).")
            self._set_status(
                f"Discarded {count} interrupted download(s). They will not be resumed.",
                "idle",
            )

    def _kick_off_resume(self, state):
        """Re-resolve the source URL and start a DownloadWorker that picks
        up from the saved segment list. Short-lived tokens get refreshed
        through the extractor system."""
        try:
            self._resume_candidates = [
                s for s in (self._resume_candidates or [])
                if s.output_dir != state.output_dir
            ]
            self._refresh_resume_banner()
            # Re-resolve when we have a usable source URL so that expired
            # playlist tokens (common on Kick/Twitch, which rotate roughly
            # every 24h) get refreshed before ffmpeg hits them with a 403.
            refreshed_url = state.playlist_url
            refreshed_audio = state.audio_url
            refreshed_tracks = list(state.selected_tracks or [])
            if state.source_url:
                ext = Extractor.detect(state.source_url)
                if ext:
                    try:
                        info = ext.resolve(state.source_url, log_fn=self._log)
                    except Exception as e:
                        self._log(f"[RESUME] Re-resolve failed, trying saved URL: {e}")
                        info = None
                    if info and info.qualities:
                        # Prefer a quality matching the saved name; fall back
                        # to the top listed quality.
                        chosen = info.qualities[0]
                        for q in info.qualities:
                            if q.name == state.quality_name:
                                chosen = q
                                break
                        refreshed_url = chosen.url or refreshed_url
                        refreshed_audio = chosen.audio_url or refreshed_audio
                        fresh_tracks = {
                            track.id: track
                            for track in list(getattr(chosen, "tracks", []) or [])
                            if getattr(track, "id", "")
                        }
                        if refreshed_tracks and fresh_tracks:
                            refreshed_tracks = [
                                fresh_tracks.get(str(track.get("id", ""))) or track
                                for track in refreshed_tracks
                            ]
                        state.playlist_url = refreshed_url
                        state.audio_url = refreshed_audio
                        state.selected_tracks = [
                            DownloadWorker._track_record(track)
                            for track in refreshed_tracks
                        ]
                        save_resume_state(state)
            remaining = remaining_segments(state)
            if not remaining:
                self._log(f"[RESUME] Nothing to resume in {state.output_dir} — clearing sidecar.")
                clear_resume_state(state.output_dir)
                return
            self._log(
                f"[RESUME] Resuming {state.title or state.output_dir} — "
                f"{len(state.completed or [])}/{len(state.segments or [])} already done."
            )
            # Minimal context — the on_all_done path tolerates a missing
            # info object and reads title/channel from self._active_stream_info
            # only when present.
            self._set_download_context(
                out_dir=state.output_dir,
                quality_name=state.quality_name,
                history_url=state.source_url,
                info=None,
            )
            worker = DownloadWorker(
                refreshed_url or state.playlist_url,
                remaining,
                state.output_dir,
                format_type=state.format_type or "hls",
            )
            worker.audio_url = refreshed_audio or ""
            worker.selected_tracks = refreshed_tracks
            worker.ytdlp_source = state.ytdlp_source or ""
            worker.ytdlp_format = state.ytdlp_format or ""
            worker.ytdlp_format_sort = state.ytdlp_format_sort or ""
            worker.ytdlp_container = state.ytdlp_container or "mp4"
            worker.ytdlp_audio_format = state.ytdlp_audio_format or ""
            worker.ytdlp_audio_quality = state.ytdlp_audio_quality or ""
            worker.download_subs = bool(state.download_subs)
            worker.capture_youtube_chat = bool(state.capture_youtube_chat)
            worker.subtitle_languages = state.subtitle_languages or ""
            worker.subtitle_auto = bool(state.subtitle_auto)
            worker.subtitle_convert = state.subtitle_convert or ""
            worker.subtitle_embed = bool(state.subtitle_embed)
            worker.sponsorblock = bool(state.sponsorblock)
            worker.sponsorblock_mark = state.sponsorblock_mark or ""
            worker.sponsorblock_remove = state.sponsorblock_remove or ""
            worker.sponsorblock_api = state.sponsorblock_api or ""
            worker.download_archive = state.download_archive or ""
            worker.break_on_existing = bool(state.break_on_existing)
            from streamkeep.download_options import (
                apply_external_downloader_options, apply_ytdlp_transfer_options,
            )
            apply_ytdlp_transfer_options(worker, state)
            apply_external_downloader_options(worker, state)
            worker.ytdlp_template_name = state.ytdlp_template_name or ""
            from streamkeep.download_options import resolve_ytdlp_arg_template
            worker.ytdlp_template_args = resolve_ytdlp_arg_template(
                self._config.get("ytdlp_arg_templates", {}),
                worker.ytdlp_template_name,
            )
            worker.cookies_browser = YtDlpExtractor.cookies_browser
            worker.rate_limit = YtDlpExtractor.rate_limit
            worker.proxy = YtDlpExtractor.proxy
            worker.parallel_connections = self._parallel_connections
            worker.progress.connect(self._on_dl_progress)
            worker.segment_done.connect(self._on_segment_done)
            worker.error.connect(self._on_dl_error)
            worker.log.connect(self._log)
            worker.all_done.connect(self._on_all_done)
            self.download_worker = worker
            self._attach_resume_to_worker(worker, resume_existing=state)
            self._set_status(
                f"Resuming {(state.title or 'download')[:60]}...",
                "processing",
            )
            worker.start()
        except Exception as e:
            self._log(f"[RESUME] Could not resume: {e}")
            self._set_status("Resume failed — see log for details.", "error")


    # Download-tab handlers moved to DownloadTabMixin in
    # streamkeep.ui.tabs.download:
    #   _output_contains_media, _output_size_label, _postprocess_snapshot, _foreground_busy,
    #   _queue_status_changed, _set_queue_item_status, _release_queue_item, _compute_next_fire,
    #   _resolve_history_url,

    def _start_next_background_job(self):
        resolver = getattr(self, "_auto_record_resolve_worker", None)
        if resolver is not None and resolver.isRunning():
            return
        if self._drain_pending_auto_records():
            return
        self._advance_queue()
        self._maybe_fire_queue_complete_power_action()
