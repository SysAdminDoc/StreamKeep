"""Queue, scheduling, and failure-recovery handlers for the Download tab."""

import os
from datetime import datetime, timedelta
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QProgressBar,
    QTableWidgetItem,
    QVBoxLayout,
)

from ... import db as _db
from ...extractors import YtDlpExtractor
from ...models import ResumeState
from ...postprocess import AUDIO_EXTS, VIDEO_EXTS, PostProcessor
from ...theme import CAT
from ...utils import (
    build_template_context as _build_template_context,
    default_output_dir as _default_output_dir,
    estimate_download_bytes as _estimate_download_bytes,
    fmt_size as _fmt_size,
    free_space_bytes as _free_space_bytes,
    render_template as _render_template,
    safe_filename as _safe_filename,
)
from ...workers import DownloadWorker, FetchWorker
from ...i18n import TranslatableDialog
from ..widgets import ask_premium_confirmation, ask_premium_text_input


class DownloadQueueMixin:
    """Persistent queue, scheduled work, and retry/discard orchestration."""

    def _on_batch_url_import(self):
        """Import URLs from a text file or clipboard paste and queue them (F44)."""
        from PyQt6.QtWidgets import (
            QDialog, QDialogButtonBox, QFileDialog, QPlainTextEdit,
            QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
        )
        import re as _re

        dlg = TranslatableDialog(self)
        dlg.setWindowTitle("Batch URL Import")
        dlg.setMinimumSize(600, 400)
        layout = QVBoxLayout(dlg)

        hint = QLabel(
            "Paste URLs below (one per line) or load from a text or .har file.\n"
            "A HAR capture is scanned for media/manifest URLs.\n"
            "Lines starting with # are comments and will be skipped."
        )
        layout.addWidget(hint)

        text_edit = QPlainTextEdit()
        text_edit.setPlaceholderText("https://twitch.tv/videos/123456\nhttps://kick.com/channel\n# this is a comment")
        layout.addWidget(text_edit)

        btn_row = QHBoxLayout()
        load_btn = QPushButton("Load from file...")
        load_btn.setObjectName("secondary")

        def _on_load_file():
            path, _ = QFileDialog.getOpenFileName(
                dlg, "Open URL list or HAR capture", "",
                "URL lists and HAR (*.txt *.har);;Text files (*.txt);;"
                "HAR captures (*.har);;All files (*)",
            )
            if not path:
                return
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    raw = f.read()
            except Exception as e:
                self._set_status(f"Failed to read file: {e}", "error")
                return
            if path.lower().endswith(".har") or raw.lstrip().startswith("{"):
                try:
                    from ...har import parse_har
                    links = parse_har(raw)
                except ValueError:
                    links = []
                if links:
                    text_edit.setPlainText(
                        "\n".join(link["url"] for link in links)
                    )
                    status_label.setText(
                        f"Extracted {len(links)} media/manifest URL(s) from HAR"
                    )
                    return
                if path.lower().endswith(".har"):
                    self._set_status(
                        "No media/manifest URLs found in the HAR capture.",
                        "warning",
                    )
                    return
            text_edit.setPlainText(raw)

        load_btn.clicked.connect(_on_load_file)
        btn_row.addWidget(load_btn)

        status_label = QLabel("")
        btn_row.addWidget(status_label, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btn_row.addWidget(buttons)
        layout.addLayout(btn_row)

        _url_re = _re.compile(r"^https?://\S+$", _re.IGNORECASE)

        def _update_count():
            lines = text_edit.toPlainText().strip().splitlines()
            valid = sum(1 for ln in lines if _url_re.match(ln.strip()))
            total = sum(1 for ln in lines if ln.strip() and not ln.strip().startswith("#"))
            invalid = total - valid
            parts = [f"{valid} valid URL(s)"]
            if invalid:
                parts.append(f"{invalid} invalid")
            status_label.setText("  ".join(parts))

        text_edit.textChanged.connect(_update_count)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        lines = text_edit.toPlainText().strip().splitlines()
        added = 0
        skipped = 0
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if not _url_re.match(line):
                skipped += 1
                continue
            ok = self._queue_add(url=line)
            if ok:
                added += 1
            else:
                skipped += 1

        self._refresh_queue_table()
        self._persist_config()
        msg = f"Queued {added} URL(s)"
        if skipped:
            msg += f", skipped {skipped} (invalid or duplicate)"
        self._set_status(msg, "success" if added else "warning")
        self._log(f"[BATCH] {msg}")


    # ── Queue URL / schedule ────────────────────────────────────

    def _on_queue_url(self):
        """Add the current URL input to the persistent queue."""
        url = self.url_input.text().strip()
        if not url:
            self._set_status("Paste a URL first.", "warning")
            return
        req = getattr(self, "_last_fetch_request", {})
        vod_source = str(req.get("vod_source", "") or "")
        vod_platform = str(req.get("vod_platform", "") or "")
        vod_title = str(req.get("vod_title", "") or "")
        vod_channel = str(req.get("vod_channel", "") or "")
        title = ""
        platform = ""
        if self.stream_info:
            title = self.stream_info.title or ""
            platform = self.stream_info.platform or ""
        queue_url = vod_source or url
        added = self._queue_add(
            queue_url,
            title=title,
            platform=platform,
            vod_source=vod_source,
            vod_platform=vod_platform,
            vod_title=vod_title or title,
            vod_channel=vod_channel or (self.stream_info.channel if self.stream_info else ""),
            ytdlp_template_name=(
                self.adv_ytdlp_template_combo.currentData() or ""
            ),
        )
        if added:
            self._set_status(f"Queued: {title or queue_url[:60]}", "success")
        else:
            self._set_status("URL already in the queue.", "warning")

    def _on_schedule_url(self):
        """Queue the current URL with a deferred start time."""
        url = self.url_input.text().strip()
        if not url:
            self._set_status("Paste a URL first.", "warning")
            return
        req = getattr(self, "_last_fetch_request", {})
        vod_source = str(req.get("vod_source", "") or "")
        vod_platform = str(req.get("vod_platform", "") or "")
        vod_title = str(req.get("vod_title", "") or "")
        vod_channel = str(req.get("vod_channel", "") or "")

        def _validate_offset(value):
            try:
                minutes = int(value)
            except (TypeError, ValueError):
                return False, "Enter a whole number of minutes."
            if not 1 <= minutes <= 60 * 24 * 30:
                return False, "Choose a delay between 1 minute and 30 days."
            return True, ""

        offset_text, ok = ask_premium_text_input(
            self,
            title="Schedule this download",
            body="Delay the next capture so it starts later without leaving the queue unmanaged.",
            eyebrow="DOWNLOAD",
            badge_text="Scheduled",
            tone="info",
            summary_title="Use minutes for the delay.",
            summary_body="Examples: 60 for one hour, 180 for three hours, or 1440 for one day.",
            field_label="Start delay (minutes)",
            field_hint="The item will stay queued until its scheduled start time arrives.",
            placeholder="60",
            text="60",
            primary_label="Schedule download",
            secondary_label="Cancel",
            validator=_validate_offset,
        )
        if not ok:
            return
        offset_min = int(offset_text)
        start_at = (datetime.now() + timedelta(minutes=offset_min)).replace(microsecond=0)
        title = ""
        platform = ""
        if self.stream_info:
            title = self.stream_info.title or ""
            platform = self.stream_info.platform or ""
        queue_url = vod_source or url
        added = self._queue_add(
            queue_url,
            title=title,
            platform=platform,
            start_at=start_at.isoformat(),
            vod_source=vod_source,
            vod_platform=vod_platform,
            vod_title=vod_title or title,
            vod_channel=vod_channel or (self.stream_info.channel if self.stream_info else ""),
            ytdlp_template_name=(
                self.adv_ytdlp_template_combo.currentData() or ""
            ),
        )
        if added:
            self._set_status(
                f"Scheduled for {start_at.strftime('%Y-%m-%d %H:%M')}: {title or queue_url[:60]}",
                "success",
            )
        else:
            self._set_status("URL already in the queue.", "warning")

    def _on_clear_queue(self):
        active = self._queue_active_item
        removable = [q for q in self._download_queue if q is not active]
        if not removable:
            self._set_status("Queue is already empty.", "info")
            return
        if len(removable) > 1 and not ask_premium_confirmation(
            self,
            title="Clear the download queue?",
            body=(
                f"Remove {len(removable)} queued job(s). Any download in progress "
                "keeps running."
            ),
            eyebrow="QUEUE",
            badge_text="Cannot be undone",
            tone="warning",
            primary_label="Clear queue",
            secondary_label="Cancel",
            default_action="secondary",
        ):
            return
        self._download_queue = [q for q in self._download_queue if q is active]
        self._persist_config()
        self._refresh_queue_table()
        self._set_status("Queue cleared.", "success")


    # ── Queue management ────────────────────────────────────────

    def _normalize_queue_item(self, item):
        if not isinstance(item, dict):
            return None
        url = str(item.get("url", "") or "").strip()
        if not url:
            return None
        title = str(item.get("title", "") or "")
        platform = str(item.get("platform", "") or "?")
        vod_source = str(item.get("vod_source", "") or "").strip()
        vod_platform = str(item.get("vod_platform", "") or "").strip()
        vod_title = str(item.get("vod_title", "") or "").strip()
        vod_channel = str(item.get("vod_channel", "") or "").strip()
        feed_url = str(item.get("feed_url", "") or "").strip()
        if not vod_source and url.isdigit() and platform.lower() == "twitch":
            # Older queue entries stored Twitch VOD IDs as plain URLs, which
            # breaks auto-start because extractor detection expects an actual URL.
            vod_source = url
        if vod_source and not vod_platform:
            vod_platform = platform
        if not vod_title:
            vod_title = title
        normalized = {
            "job_id": str(item.get("job_id", "") or ""),
            "url": url,
            "title": title or vod_title or url,
            "platform": platform,
            "status": str(item.get("status", "queued") or "queued"),
            "added": str(item.get("added", "") or ""),
            "note": str(item.get("note", "") or ""),
            "start_at": str(item.get("start_at", "") or ""),
            "vod_source": vod_source,
            "vod_platform": vod_platform,
            "vod_title": vod_title,
            "vod_channel": vod_channel,
            "feed_url": feed_url,
            "download_archive": str(
                item.get("download_archive", "") or ""
            ),
            "break_on_existing": bool(item.get("break_on_existing", False)),
            "ytdlp_template_name": str(
                item.get("ytdlp_template_name", "") or ""
            ),
        }
        vod_date = str(item.get("vod_date", "") or "")
        if vod_date:
            normalized["vod_date"] = vod_date
        try:
            failure_id = int(item.get("failure_id", 0) or 0)
        except (TypeError, ValueError):
            failure_id = 0
        if failure_id:
            normalized["failure_id"] = failure_id
        for key in (
            "quality", "container", "thumbnail_path", "thumbnail_url",
            "progress", "speed", "eta", "size", "size_bytes",
        ):
            value = item.get(key)
            if value not in (None, ""):
                normalized[key] = value
        return normalized

    def _queue_add(
        self,
        url,
        title="",
        platform="",
        note="",
        start_at="",
        vod_source="",
        vod_platform="",
        vod_title="",
        vod_channel="",
        feed_url="",
        download_archive="",
        break_on_existing=False,
        ytdlp_template_name="",
    ):
        """Append a URL to the persistent download queue.
        If start_at (ISO timestamp) is set, the item will only be picked
        up by _advance_queue after that time."""
        if not url:
            return False
        item_key = str(vod_source or url)
        if any(
            (q.get("vod_source") or q.get("url")) == item_key
            and q.get("status") not in ("failed", "cancelled")
            for q in self._download_queue
        ):
            return False
        try:
            from ...download_options import resolve_ytdlp_arg_template
            resolve_ytdlp_arg_template(
                self._config.get("ytdlp_arg_templates", {}),
                ytdlp_template_name,
            )
        except ValueError as error:
            self._set_status(str(error), "warning")
            return False
        self._download_queue.append(self._normalize_queue_item({
            "url": url,
            "title": title or vod_title or url,
            "platform": platform or "?",
            "status": "queued",
            "added": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "note": note,
            "start_at": start_at,
            "vod_source": vod_source,
            "vod_platform": vod_platform,
            "vod_title": vod_title or title,
            "vod_channel": vod_channel,
            "feed_url": feed_url,
            "download_archive": download_archive,
            "break_on_existing": break_on_existing,
            "ytdlp_template_name": ytdlp_template_name,
        }))
        self._persist_config()
        if hasattr(self, "queue_table"):
            self._refresh_queue_table()
        return True

    def _queue_remove(self, idx):
        if 0 <= idx < len(self._download_queue):
            item = self._download_queue[idx]
            if item is self._queue_active_item or item.get("status") in ("fetching", "downloading"):
                self._set_status("The active queue job cannot be removed while it is running.", "warning")
                return None
            removed = self._download_queue.pop(idx)
            self._persist_config()
            if hasattr(self, "queue_table"):
                self._refresh_queue_table()
            return removed
        return None

    def _active_queue_download_count(self):
        """Return the number of currently active queue downloads + fetches."""
        active = len([w for w in self._queue_workers.values() if w.isRunning()])
        active += len([w for w in self._queue_fetch_workers.values() if w.isRunning()])
        # Count the legacy single-worker path too
        if self._queue_active_item is not None:
            active += 1
        return active

    def _advance_queue(self):
        """Start the next queued item(s) up to the concurrent download limit.
        Scheduled items (start_at in the future) are skipped."""
        # Legacy foreground worker blocks legacy queue path
        worker = getattr(self, "download_worker", None)
        fg_busy = worker is not None and worker.isRunning()
        # Check concurrent capacity
        cap = max(1, int(self._max_concurrent_downloads))
        active = self._active_queue_download_count()
        if active >= cap:
            return
        # Also block if legacy single-worker fetch is running and there's
        # an active queue item using the old path
        if self._queue_active_item is not None and fg_busy:
            return
        now = datetime.now()
        ready = []
        active_ids = set(self._queue_workers.keys()) | set(self._queue_fetch_workers.keys())
        for q in self._download_queue:
            if q.get("status") != "queued":
                continue
            if id(q) in active_ids:
                continue
            start_at = q.get("start_at", "")
            if start_at:
                try:
                    ts = datetime.fromisoformat(start_at)
                    if ts > now:
                        continue
                except Exception:
                    pass
            ready.append(q)
            if active + len(ready) >= cap:
                break
        if not ready:
            return
        for item in ready:
            self._start_queue_item(item)

    def _maybe_fire_queue_complete_power_action(self):
        """Fire the configured power action once when the queue drains (V24)."""
        if not getattr(self, "_power_action_armed", False):
            return
        if self._active_queue_download_count() > 0:
            return
        # If _advance_queue left nothing active, only future-scheduled items
        # (if any) remain; the current batch is complete.
        self._power_action_armed = False
        from ...power import normalize_power_action, run_queue_complete_action

        action = normalize_power_action(
            self._config.get("queue_complete_action", "none")
        )
        if action == "none":
            return

        def _notify():
            self._notify(
                "StreamKeep — Queue complete",
                "All queued downloads finished.",
            )

        def _hook():
            self._fire_hook("queue_complete")

        run_queue_complete_action(
            action, notify_fn=_notify, hook_fn=_hook, log_fn=self._log,
        )
        if action != "notify":
            self._log(f"[POWER] Queue-complete action '{action}' dispatched.")

    def _start_queue_item(self, item):
        """Launch a fetch→download pipeline for a single queue item using
        a dedicated FetchWorker (concurrent, doesn't touch the UI state)."""
        item_id = id(item)
        # Arm the queue-complete power action for this batch (V24).
        self._power_action_armed = True
        self._set_queue_item_status(item, "fetching")
        self._log(f"[QUEUE] Starting: {item.get('title', '')[:60]}")
        # Use a dedicated FetchWorker that doesn't share the foreground UI state
        url = item.get("vod_source") or item["url"]
        fetch = FetchWorker(url)
        fetch.finished.connect(
            lambda info, it=item: self._on_queue_fetch_done(it, info)
        )
        fetch.error.connect(
            lambda err, it=item: self._on_queue_fetch_error(it, err)
        )
        self._queue_fetch_workers[item_id] = fetch
        fetch.start()

    def _on_queue_fetch_done(self, item, info):
        """Handle fetch completion for a concurrent queue item."""
        item_id = id(item)
        fw = self._queue_fetch_workers.pop(item_id, None)
        if fw and not fw.isRunning():
            try:
                fw.wait(200)
            except Exception:
                pass
        if info is None:
            job_id = self._record_failed_job(
                stage="fetch",
                error="Fetch returned no data",
                item=item,
            )
            if job_id:
                item["failure_id"] = job_id
            self._set_queue_item_status(item, "failed", "Fetch returned no data")
            self._log(f"[QUEUE] Fetch failed: {item.get('title', '')[:60]}")
            self._advance_queue()
            return
        # Carry the originating podcast feed from the queue item onto the
        # resolved info so finalize can fetch this episode's sidecars.
        if not getattr(info, "feed_url", "") and item.get("feed_url"):
            info.feed_url = item["feed_url"]
        # Pick the best quality
        q_data = None
        if info.qualities:
            q_data = info.qualities[0]  # Highest quality (pre-sorted)
        if q_data is None and not info.url:
            job_id = self._record_failed_job(
                stage="fetch",
                error="No playable quality",
                item=item,
                info=info,
            )
            if job_id:
                item["failure_id"] = job_id
            self._set_queue_item_status(item, "failed", "No playable quality")
            self._advance_queue()
            return
        # Build segments and output path
        playlist_url = q_data.url if q_data else info.url
        fmt_type = q_data.format_type if q_data else "hls"
        audio_url = q_data.audio_url if q_data else ""
        ytdlp_source = q_data.ytdlp_source if q_data else ""
        ytdlp_format = q_data.ytdlp_format if q_data else ""
        is_live = info.is_live or info.total_secs <= 0
        title_safe = _safe_filename(info.title or item.get("title") or "download")
        segments = [(0, title_safe, 0, 0 if is_live else int(info.total_secs))]
        ctx = _build_template_context(info)
        folder_parts = _render_template(self._folder_template, ctx)
        base_out = self.output_input.text().strip() or str(_default_output_dir())
        out_dir = os.path.join(base_out, *folder_parts) if folder_parts else base_out
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as e:
            job_id = self._record_failed_job(
                stage="download",
                error=f"Cannot create dir: {e}",
                item=item,
                info=info,
                out_dir=out_dir,
            )
            if job_id:
                item["failure_id"] = job_id
            self._set_queue_item_status(item, "failed", f"Cannot create dir: {e}")
            self._advance_queue()
            return
        # Create and start the DownloadWorker
        self._set_queue_item_status(item, "downloading")
        item["quality"] = (
            (q_data.resolution or q_data.name) if q_data else "Best available"
        )
        item["container"] = (
            (q_data.format_type if q_data else fmt_type).replace("ytdlp_direct", "MP4").upper()
        )
        item["thumbnail_url"] = str(getattr(info, "thumbnail_url", "") or "")
        self._log(f"[QUEUE] Downloading: {info.title or item.get('title', '')[:60]} → {out_dir}")
        worker = DownloadWorker(playlist_url or "", segments, out_dir, format_type=fmt_type)
        worker.audio_url = audio_url
        if q_data:
            from ...models import default_media_tracks
            worker.selected_tracks = default_media_tracks(q_data)
        worker.ytdlp_source = ytdlp_source
        worker.ytdlp_format = ytdlp_format
        worker.cookies_browser = YtDlpExtractor.cookies_browser
        # Share bandwidth across concurrent workers
        rl = YtDlpExtractor.rate_limit
        active_count = self._active_queue_download_count() + 1
        if rl and active_count > 1:
            try:
                import re as _re
                # Parse yt-dlp rate limit format: number + optional suffix
                m = _re.match(r'^([\d.]+)\s*([KkMmGg])?', str(rl))
                if m:
                    val = float(m.group(1))
                    suffix = (m.group(2) or "").upper()
                    multiplier = {"K": 1_000, "M": 1_000_000, "G": 1_000_000_000}.get(suffix, 1)
                    rl_bytes = int(val * multiplier)
                    shared = max(100_000, rl_bytes // active_count)
                    rl = str(shared)
            except (ValueError, AttributeError):
                pass
        worker.rate_limit = rl
        worker.proxy = YtDlpExtractor.proxy
        worker.download_subs = YtDlpExtractor.download_subs
        worker.capture_youtube_chat = YtDlpExtractor.capture_youtube_chat
        worker.subtitle_languages = YtDlpExtractor.subtitle_languages
        worker.subtitle_auto = YtDlpExtractor.subtitle_auto
        worker.subtitle_convert = YtDlpExtractor.subtitle_convert
        worker.subtitle_embed = YtDlpExtractor.subtitle_embed
        worker.sponsorblock = YtDlpExtractor.sponsorblock
        worker.sponsorblock_mark = YtDlpExtractor.sponsorblock_mark
        worker.sponsorblock_remove = YtDlpExtractor.sponsorblock_remove
        worker.sponsorblock_api = YtDlpExtractor.sponsorblock_api
        from ...download_options import (
            apply_external_downloader_options,
            apply_ytdlp_transfer_options, resolve_ytdlp_arg_template,
        )
        apply_ytdlp_transfer_options(worker, YtDlpExtractor)
        apply_external_downloader_options(worker, YtDlpExtractor)
        worker.ytdlp_template_name = str(
            item.get("ytdlp_template_name", "") or ""
        )
        try:
            worker.ytdlp_template_args = resolve_ytdlp_arg_template(
                self._config.get("ytdlp_arg_templates", {}),
                worker.ytdlp_template_name,
            )
        except ValueError as error:
            failure_id = self._record_failed_job(
                stage="download", error=str(error), item=item,
                info=info, out_dir=out_dir,
            )
            if failure_id:
                item["failure_id"] = failure_id
            self._set_queue_item_status(item, "failed", str(error))
            self._log(f"[QUEUE] {error}")
            self._advance_queue()
            return
        worker.download_archive = str(item.get("download_archive", "") or "")
        worker.break_on_existing = bool(item.get("break_on_existing", False))
        worker.parallel_connections = self._parallel_connections
        worker.log.connect(self._log)
        worker.progress.connect(
            lambda _segment, pct, status, it=item:
            self._on_queue_item_progress(it, pct, status)
        )
        worker.segment_done.connect(
            lambda _segment, size, it=item:
            self._on_queue_item_segment_done(it, size)
        )
        worker.all_done.connect(lambda it=item, inf=info: self._on_queue_item_done(it, inf, out_dir))
        worker.error.connect(lambda _idx, err, it=item: self._on_queue_item_error(it, err))
        self._queue_workers[item_id] = worker
        self._queue_contexts[item_id] = {
            "out_dir": out_dir, "info": info,
            "q_name": q_data.name if q_data else "",
        }
        worker.start()
        self._update_tray_badge()
        self._refresh_queue_table()
        # Try to fill remaining slots
        self._advance_queue()

    def _on_queue_fetch_error(self, item, err):
        """Handle fetch error for a concurrent queue item."""
        item_id = id(item)
        fw = self._queue_fetch_workers.pop(item_id, None)
        # Join the thread to prevent resource leaks (mirrors _on_queue_fetch_done)
        if fw:
            try:
                fw.wait(500)
            except Exception:
                pass
        job_id = self._record_failed_job(
            stage="fetch",
            error=err,
            item=item,
        )
        if job_id:
            item["failure_id"] = job_id
        self._set_queue_item_status(item, "failed", str(err)[:120])
        self._log(f"[QUEUE] Fetch error for {item.get('title', '')[:60]}: {err}")
        self._advance_queue()

    def _on_queue_item_done(self, item, info, out_dir):
        """Handle download completion for a concurrent queue item."""
        item_id = id(item)
        ctx = self._queue_contexts.pop(item_id, {})
        worker = self._queue_workers.pop(item_id, None)
        if worker and not worker.isRunning():
            try:
                worker.wait(500)
            except Exception:
                pass
        title = info.title if info else item.get("title", "Download")
        self._log(f"[QUEUE] Complete: {title[:60]}")
        self._notify_center(f"Queue download complete: {title[:50]}", "success")
        self._fire_hook("download_complete", title=title)
        # Save metadata + history entry
        q_name = ctx.get("q_name", "")
        self._save_metadata(out_dir, q_name, history_url=item.get("url", ""), info=info)
        self._media_server_import(out_dir, info)
        # Handle recurrence or mark done
        rec = (item.get("recurrence") or "").strip()
        if rec:
            next_fire = self._compute_next_fire(item)
            if next_fire:
                item["status"] = "queued"
                item["start_at"] = next_fire.isoformat()
                item["note"] = f"recurring ({rec}) — next fire {next_fire.strftime('%Y-%m-%d %H:%M')}"
            else:
                item["status"] = "done"
        else:
            item["status"] = "done"
        failure_id = int(item.get("failure_id", 0) or 0)
        if failure_id:
            _db.mark_failed_job_resolved(failure_id)
        _db.mark_failed_jobs_resolved_for_url(item.get("url", ""))
        self._queue_status_changed()
        self._update_tray_badge()
        self._advance_queue()

    def _on_queue_item_error(self, item, err):
        """Handle download error for a concurrent queue item."""
        item_id = id(item)
        ctx = self._queue_contexts.pop(item_id, {})
        worker = self._queue_workers.pop(item_id, None)
        if worker and not worker.isRunning():
            try:
                worker.wait(500)
            except Exception:
                pass
        job_id = self._record_failed_job(
            stage="download",
            error=err,
            item=item,
            out_dir=ctx.get("out_dir", "") if isinstance(ctx, dict) else "",
            info=ctx.get("info") if isinstance(ctx, dict) else None,
        )
        if job_id:
            item["failure_id"] = job_id
        self._set_queue_item_status(item, "failed", str(err)[:120])
        self._log(f"[QUEUE] Error: {item.get('title', '')[:60]} — {err}")
        self._advance_queue()

    @staticmethod
    def _queue_status_color(status, is_scheduled=False):
        if is_scheduled:
            return CAT["peach"]
        if status in ("fetching", "downloading"):
            return CAT["accent"]
        if status in ("done", "ready"):
            return CAT["accentSoft"]
        if status in ("failed", "cancelled"):
            return CAT["red"]
        if status == "paused":
            return CAT["muted"]
        return CAT["gold"]

    @staticmethod
    def _queue_thumbnail(item):
        path = str(item.get("thumbnail_path", "") or "")
        pixmap = QPixmap(path) if path and os.path.isfile(path) else QPixmap()
        if not pixmap.isNull():
            return pixmap.scaled(
                112, 62,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )

        # A deterministic abstract preview keeps rows scannable before remote
        # metadata has supplied artwork; it is intentionally quiet, not a
        # platform badge or fake media frame.
        pixmap = QPixmap(112, 62)
        pixmap.fill(QColor(CAT["surface0"]))
        painter = QPainter(pixmap)
        seed = sum(ord(char) for char in str(item.get("title", "")))
        gradient = QLinearGradient(0, 0, 112, 62)
        gradient.setColorAt(0.0, QColor(CAT["panelHi"]))
        gradient.setColorAt(1.0, QColor(CAT["accent"]).darker(170 + seed % 35))
        painter.fillRect(pixmap.rect(), gradient)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(CAT["accent"]), 2))
        for offset in range(-20, 150, 18):
            rise = 10 + ((seed + offset) % 22)
            painter.drawLine(offset, 62, offset + 44, rise)
        painter.end()
        return pixmap

    def _queue_name_widget(self, item, check):
        frame = QFrame()
        frame.setObjectName("queueName")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(8, 7, 8, 7)
        layout.setSpacing(10)

        layout.addWidget(check)

        thumbnail = QLabel()
        thumbnail.setObjectName("queueThumbnail")
        thumbnail.setFixedSize(112, 62)
        thumbnail.setPixmap(self._queue_thumbnail(item))
        thumbnail.setScaledContents(True)
        layout.addWidget(thumbnail)

        copy = QVBoxLayout()
        copy.setContentsMargins(0, 0, 0, 0)
        copy.setSpacing(3)
        title = QLabel(str(item.get("title", "") or item.get("url", ""))[:80])
        title.setObjectName("queueTitle")
        title.setToolTip(str(item.get("title", "") or item.get("url", "")))
        meta_parts = [
            str(item.get("quality", "") or "").strip(),
            str(item.get("container", "") or "").strip(),
        ]
        meta = QLabel("  ·  ".join(part for part in meta_parts if part) or str(item.get("platform", "?")))
        meta.setObjectName("queueMeta")
        copy.addStretch(1)
        copy.addWidget(title)
        copy.addWidget(meta)
        copy.addStretch(1)
        layout.addLayout(copy, 1)
        return frame

    def _queue_status_widget(self, status, display_status, color):
        frame = QFrame()
        frame.setObjectName("queueStatus")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(8, 0, 4, 0)
        layout.setSpacing(7)
        dot = QLabel("●")
        dot.setStyleSheet(f"color: {color}; font-size: 11px;")
        text = QLabel(display_status)
        text.setObjectName("queueStatusText")
        text.setToolTip(status.replace("_", " ").strip().title())
        layout.addWidget(dot)
        layout.addWidget(text)
        layout.addStretch(1)
        return frame

    def _queue_progress_widget(self, item):
        frame = QFrame()
        frame.setObjectName("queueProgress")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(8, 0, 6, 0)
        layout.setSpacing(7)
        progress = QProgressBar()
        progress.setObjectName("queueProgressBar")
        progress.setRange(0, 100)
        progress.setValue(max(0, min(100, int(item.get("progress", 0) or 0))))
        progress.setTextVisible(False)
        progress.setFixedWidth(76)
        progress.setAccessibleName(f"Progress for {item.get('title', 'queue item')}")
        label = QLabel(f"{progress.value()}%")
        label.setObjectName("queueProgressText")
        layout.addWidget(progress)
        layout.addWidget(label)
        layout.addStretch(1)
        return frame, progress, label

    def _selected_queue_items(self):
        return [q for q in self._download_queue if q.get("_ui_selected", True)]

    def _on_queue_item_selected(self, item, checked):
        item["_ui_selected"] = bool(checked)
        self._refresh_queue_toolbar()

    def _on_queue_header_clicked(self, section):
        if section != 0 or not self._download_queue:
            return
        checked = not all(q.get("_ui_selected", True) for q in self._download_queue)
        for item in self._download_queue:
            item["_ui_selected"] = checked
        self._refresh_queue_table()

    def _refresh_queue_toolbar(self):
        selected = self._selected_queue_items()
        if hasattr(self, "queue_selected_label"):
            self.queue_selected_label.setText(f"{len(selected)} selected")
        statuses = {str(item.get("status", "queued")) for item in selected}
        locked = {"fetching", "downloading"}
        removable = any(str(item.get("status", "queued")) not in locked for item in selected)
        if hasattr(self, "queue_start_btn"):
            self.queue_start_btn.setEnabled(bool(selected) and bool(statuses & {"queued", "paused"}))
            self.queue_pause_btn.setEnabled(bool(selected) and "queued" in statuses)
            self.queue_remove_btn.setEnabled(removable)
            self.queue_retry_btn.setEnabled(bool(statuses & {"failed", "cancelled"}))
            self.queue_pause_all_btn.setEnabled(
                any(item.get("status", "queued") == "queued" for item in self._download_queue)
            )

    def _on_queue_start_selected(self):
        selected = self._selected_queue_items()
        changed = False
        for item in selected:
            if item.get("status") == "paused":
                item["status"] = "queued"
                changed = True
        if changed:
            self._queue_status_changed()
        self._advance_queue()
        self._set_status("Selected queue jobs are ready to start.", "success")

    def _on_queue_pause_selected(self):
        changed = 0
        for item in self._selected_queue_items():
            if item.get("status", "queued") == "queued":
                item["status"] = "paused"
                changed += 1
        if changed:
            self._queue_status_changed()
        self._set_status(f"Held {changed} pending queue job(s).", "success" if changed else "idle")

    def _on_queue_pause_all(self):
        changed = 0
        for item in self._download_queue:
            if item.get("status", "queued") == "queued":
                item["status"] = "paused"
                changed += 1
        if changed:
            self._queue_status_changed()
        self._set_status(f"Held {changed} pending queue job(s).", "success" if changed else "idle")

    def _on_queue_remove_selected(self):
        locked = {"fetching", "downloading"}
        removable = [
            index for index, item in enumerate(self._download_queue)
            if item.get("_ui_selected", True) and item.get("status", "queued") not in locked
        ]
        for index in reversed(removable):
            self._download_queue.pop(index)
        if removable:
            self._queue_status_changed()
        self._set_status(
            f"Removed {len(removable)} queue job(s).",
            "success" if removable else "warning",
        )

    def _on_queue_retry_selected(self):
        retried = 0
        for item in self._selected_queue_items():
            if item.get("status") not in ("failed", "cancelled"):
                continue
            failure_id = int(item.get("failure_id", 0) or 0)
            if failure_id:
                if self._retry_failed_job(failure_id):
                    retried += 1
            else:
                item["status"] = "queued"
                item["note"] = "retry requested"
                retried += 1
        if retried:
            self._queue_status_changed()
            self._advance_queue()
        self._set_status(f"Retrying {retried} queue job(s).", "success" if retried else "warning")

    @staticmethod
    def _queue_display_bytes(item):
        try:
            explicit = int(item.get("size_bytes", 0) or 0)
        except (TypeError, ValueError):
            explicit = 0
        if explicit > 0:
            return explicit
        text = str(item.get("size", "") or "").strip().upper().replace("IB", "B")
        import re
        match = re.search(r"([\d.]+)\s*(KB|MB|GB|TB|B)", text)
        if not match:
            return 0
        value = float(match.group(1))
        scale = {"B": 1, "KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3, "TB": 1024 ** 4}
        return int(value * scale[match.group(2)])

    def _refresh_queue_footer(self):
        if not hasattr(self, "queue_footer_meta"):
            return
        total = len(self._download_queue)
        active = sum(
            item.get("status") in ("fetching", "downloading")
            for item in self._download_queue
        )
        ready = sum(
            item.get("status") in ("queued", "paused", "ready")
            for item in self._download_queue
        )
        total_bytes = sum(self._queue_display_bytes(item) for item in self._download_queue)
        parts = [
            f"{total} download{'s' if total != 1 else ''}",
            f"{active} downloading",
            f"{ready} ready",
        ]
        if total_bytes:
            parts.append(f"{_fmt_size(total_bytes)} total")
        self.queue_footer_meta.setText("   |   ".join(parts))

    def _on_queue_item_progress(self, item, percent, status):
        item["progress"] = max(0, min(100, int(percent or 0)))
        pieces = [piece.strip() for piece in str(status or "").split("|") if piece.strip()]
        for piece in pieces:
            if piece.upper().startswith("ETA "):
                item["eta"] = piece[4:].strip()
            elif "/S" in piece.upper():
                item["speed"] = piece
            elif any(unit in piece.upper() for unit in ("KB", "MB", "GB", "KIB", "MIB", "GIB")):
                item["size"] = piece.split("/")[0].strip()
        row = getattr(self, "_queue_row_by_id", {}).get(id(item))
        if row is None:
            return
        progress = getattr(self, "_queue_progress_bars", {}).get(id(item))
        label = getattr(self, "_queue_progress_labels", {}).get(id(item))
        if progress is not None:
            progress.setValue(item["progress"])
        if label is not None:
            label.setText(f"{item['progress']}%")
        for column, key in ((4, "speed"), (5, "eta"), (6, "size")):
            cell = self.queue_table.item(row, column)
            if cell is not None:
                cell.setText(str(item.get(key, "") or "—"))
        self._refresh_queue_footer()

    def _on_queue_item_segment_done(self, item, size):
        item["progress"] = 100
        item["size"] = str(size or item.get("size", ""))
        self._on_queue_item_progress(item, 100, item["size"])

    def _refresh_queue_table(self):
        if not hasattr(self, "queue_table"):
            return
        queue_count = len(self._download_queue)
        self.queue_table.setRowCount(queue_count)
        self._queue_checks = []
        self._queue_row_by_id = {}
        self._queue_progress_bars = {}
        self._queue_progress_labels = {}
        if hasattr(self, "queue_empty_state"):
            self.queue_empty_state.setVisible(queue_count == 0)
        if queue_count:
            self.queue_table.setMinimumHeight(260)
            self.queue_table.setMaximumHeight(16777215)
        else:
            self.queue_table.setMinimumHeight(48)
            self.queue_table.setMaximumHeight(58)
        now = datetime.now()
        for row, item in enumerate(self._download_queue):
            self._queue_row_by_id[id(item)] = row
            status = str(item.get("status", "queued") or "queued")
            start_at = str(item.get("start_at", "") or "")
            is_scheduled = False
            if start_at and status == "queued":
                try:
                    is_scheduled = datetime.fromisoformat(start_at) > now
                except Exception:
                    pass
            display_status = "Scheduled" if is_scheduled else status.replace("_", " ").title()
            color = self._queue_status_color(status, is_scheduled)

            check = QCheckBox()
            check.setChecked(bool(item.get("_ui_selected", True)))
            check.setAccessibleName(f"Select queue job {row + 1}: {item.get('title', '')}")
            check.toggled.connect(
                lambda checked, queue_item=item: self._on_queue_item_selected(queue_item, checked)
            )
            self._queue_checks.append(check)
            self.queue_table.setCellWidget(row, 0, self._queue_name_widget(item, check))
            source = QTableWidgetItem(str(item.get("url", "") or "—"))
            source.setToolTip(str(item.get("url", "") or ""))
            source.setForeground(QColor(CAT["muted"]))
            self.queue_table.setItem(row, 1, source)
            self.queue_table.setCellWidget(
                row, 2, self._queue_status_widget(status, display_status, color)
            )
            progress_shell, progress, progress_label = self._queue_progress_widget(item)
            self._queue_progress_bars[id(item)] = progress
            self._queue_progress_labels[id(item)] = progress_label
            self.queue_table.setCellWidget(row, 3, progress_shell)
            for column, key in ((4, "speed"), (5, "eta"), (6, "size")):
                cell = QTableWidgetItem(str(item.get(key, "") or "—"))
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.queue_table.setItem(row, column, cell)

        self._refresh_queue_toolbar()
        self._refresh_queue_footer()
        self._refresh_shell_overview()

    def _queue_move(self, idx, direction):
        """Move a queue item up (-1) or down (+1)."""
        if idx < 0 or idx >= len(self._download_queue):
            return
        target = idx + direction
        if target < 0 or target >= len(self._download_queue):
            return
        item = self._download_queue[idx]
        other = self._download_queue[target]
        locked_statuses = {"fetching", "downloading"}
        if (
            item is self._queue_active_item
            or other is self._queue_active_item
            or item.get("status") in locked_statuses
            or other.get("status") in locked_statuses
        ):
            self._set_status("The active queue job cannot be reordered while it is running.", "warning")
            return
        self._download_queue[idx], self._download_queue[target] = (
            self._download_queue[target], self._download_queue[idx]
        )
        self._persist_config()
        self._refresh_queue_table()

    @staticmethod
    def _quality_rank(quality_str):
        """Parse a quality string like '1080p', 'source', '720p60' into a
        numeric rank for comparison.  Higher is better."""
        if not quality_str:
            return 0
        q = quality_str.lower().strip()
        if q in ("source", "best", "highest"):
            return 9999
        digits = ""
        for c in q:
            if c.isdigit():
                digits += c
            elif digits:
                break
        return int(digits) if digits else 0


    # ── Queue context menu ──────────────────────────────────────

    def _on_queue_context_menu(self, pos):
        if not hasattr(self, "queue_table"):
            return
        idx = self.queue_table.indexAt(pos)
        if not idx.isValid():
            return
        row = idx.row()
        if not (0 <= row < len(self._download_queue)):
            return
        item = self._download_queue[row]
        menu = QMenu(self)
        current = (item.get("recurrence") or "").strip().lower() or "(one-shot)"
        header = menu.addAction(f"Recurrence: {current}")
        header.setEnabled(False)
        menu.addSeparator()
        retry_failure = None
        discard_failure = None
        failure_id = int(item.get("failure_id", 0) or 0)
        if item.get("status") == "failed" and failure_id:
            failure_header = menu.addAction(f"Failure #{failure_id}")
            failure_header.setEnabled(False)
            retry_failure = menu.addAction("Retry failed job")
            discard_failure = menu.addAction("Discard failure")
            menu.addSeparator()
        one_shot = menu.addAction("One-shot (no recurrence)")
        daily = menu.addAction("Daily")
        weekly = menu.addAction("Weekly")
        custom = menu.addAction("Weekday mask... (mon,tue,fri)")
        chosen = menu.exec(self.queue_table.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if retry_failure is not None and chosen == retry_failure:
            self._retry_failed_job(failure_id)
            return
        if discard_failure is not None and chosen == discard_failure:
            self._discard_failed_job(failure_id)
            return
        new_rec = ""
        if chosen == one_shot:
            new_rec = ""
        elif chosen == daily:
            new_rec = "daily"
        elif chosen == weekly:
            new_rec = "weekly"
        elif chosen == custom:
            valid_days = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}

            def _validate_mask(value):
                tokens = [tok.strip().lower() for tok in (value or "").split(",") if tok.strip()]
                if not tokens:
                    return False, "Enter at least one weekday code."
                invalid = [tok for tok in tokens if tok not in valid_days]
                if invalid:
                    return False, "Use day codes like mon,tue,wed,thu,fri,sat,sun."
                return True, ""

            text, ok = ask_premium_text_input(
                self,
                title="Repeat on specific weekdays",
                body="Use a weekday mask when a queued item should recur only on selected days.",
                eyebrow="QUEUE",
                badge_text="Recurrence",
                tone="info",
                summary_title="Comma-separated short codes are supported.",
                summary_body="Example: mon,wed,fri. Use one-shot or weekly if the item should not use a custom mask.",
                field_label="Weekday mask",
                field_hint="Supported values: mon, tue, wed, thu, fri, sat, sun.",
                text=item.get("recurrence", "") or "mon,wed,fri",
                primary_label="Save recurrence",
                secondary_label="Cancel",
                validator=_validate_mask,
            )
            if not ok:
                return
            tokens = [tok.strip().lower() for tok in text.split(",") if tok.strip()]
            new_rec = ",".join(tokens)
        item["recurrence"] = new_rec
        if new_rec:
            item["note"] = f"recurring ({new_rec})"
        else:
            item["note"] = ""
        self._queue_status_changed()
        self._set_status(
            f"Queue item recurrence set to '{new_rec or 'one-shot'}'.",
            "success",
        )

    # _on_theme_changed, _on_companion_toggled, _on_companion_scope_toggled,
    # _copy_text_to_clipboard, _companion_local_url, _refresh_companion_ui,
    # _on_copy_companion_url, _on_copy_companion_token,
    # _on_open_companion_remote
    #   -> streamkeep.ui.tabs.settings.SettingsTabMixin


    # ── Download context / helpers ──────────────────────────────

    def _set_download_context(self, out_dir="", quality_name="", history_url="", info=None):
        self._active_output_dir = out_dir
        self._active_quality_name = quality_name
        self._active_history_url = history_url
        self._active_stream_info = info or self.stream_info

    def _attach_resume_to_worker(self, worker, *, resume_existing=None, context=None):
        """Wire a ResumeState into a DownloadWorker just before start().

        If `resume_existing` is provided (from the startup-banner "Resume"
        action), its completed-segments list is preserved and re-used.
        Otherwise a fresh state is built from `context` (dict with
        source_url/platform/title/channel/quality_name keys) or, if None,
        from `self._active_*` for backwards-compat with the foreground path.
        Silent on failure — a resume sidecar is nice-to-have, never required.
        """
        try:
            if context is None:
                info = self._active_stream_info or self.stream_info
                source_url = self._active_history_url or ""
                platform = (info.platform if info else "") or ""
                title = (info.title if info else "") or ""
                channel = (info.channel if info else "") or ""
                quality_name = self._active_quality_name or ""
            else:
                info = context.get("info")
                source_url = context.get("source_url") or ""
                platform = context.get("platform") or (info.platform if info else "") or ""
                title = context.get("title") or (info.title if info else "") or ""
                channel = context.get("channel") or (info.channel if info else "") or ""
                quality_name = context.get("quality_name") or ""
            if resume_existing is not None:
                state = resume_existing
                # Refresh the parts of the sidecar that can have changed
                # between the interrupted run and now (new token URLs, etc.).
                state.playlist_url = worker.playlist_url
                state.format_type = worker.format_type
                state.audio_url = worker.audio_url or ""
                state.ytdlp_source = worker.ytdlp_source or ""
                state.ytdlp_format = worker.ytdlp_format or ""
                state.ytdlp_format_sort = worker.ytdlp_format_sort or ""
                state.ytdlp_container = worker.ytdlp_container or "mp4"
                state.ytdlp_audio_format = worker.ytdlp_audio_format or ""
                state.ytdlp_audio_quality = worker.ytdlp_audio_quality or ""
                state.download_subs = bool(worker.download_subs)
                state.capture_youtube_chat = bool(worker.capture_youtube_chat)
                state.subtitle_languages = worker.subtitle_languages or ""
                state.subtitle_auto = bool(worker.subtitle_auto)
                state.subtitle_convert = worker.subtitle_convert or ""
                state.subtitle_embed = bool(worker.subtitle_embed)
                state.sponsorblock = bool(worker.sponsorblock)
                state.sponsorblock_mark = worker.sponsorblock_mark or ""
                state.sponsorblock_remove = worker.sponsorblock_remove or ""
                state.sponsorblock_api = worker.sponsorblock_api or ""
                state.output_dir = worker.output_dir
                state.segments = [list(s) for s in worker.segments]
            else:
                state = ResumeState(
                    source_url=source_url,
                    platform=platform,
                    title=title,
                    channel=channel,
                    quality_name=quality_name,
                    output_dir=worker.output_dir,
                )
            worker.attach_resume_state(state)
        except Exception as e:
            self._log(f"[RESUME] Could not write sidecar: {e}")

    def _output_contains_media(self, out_dir):
        if not out_dir or not os.path.isdir(out_dir):
            return False
        media_exts = {ext.lower() for ext in (VIDEO_EXTS | AUDIO_EXTS)}
        try:
            for entry in os.scandir(out_dir):
                if entry.is_file() and Path(entry.name).suffix.lower() in media_exts:
                    return True
        except OSError:
            return False
        return False

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
        return _fmt_size(total) if total > 0 else ""

    def _postprocess_snapshot(self):
        keys = [
            "extract_audio",
            "normalize_loudness",
            "reencode_h265",
            "contact_sheet",
            "split_by_chapter",
            "convert_video",
            "convert_video_format",
            "convert_video_codec",
            "convert_video_scale",
            "convert_video_fps",
            "convert_audio",
            "convert_audio_format",
            "convert_audio_codec",
            "convert_audio_bitrate",
            "convert_audio_samplerate",
            "convert_delete_source",
        ]
        snap = {k: getattr(PostProcessor, k) for k in keys}
        # Apply per-download PP preset override (F18)
        overrides = getattr(self, "_dl_overrides", {})
        preset_name = overrides.get("pp_preset", "")
        if preset_name:
            from .settings import BUILTIN_PRESETS, _get_user_presets
            all_presets = dict(BUILTIN_PRESETS)
            all_presets.update(_get_user_presets(self))
            if preset_name in all_presets:
                snap.update(all_presets[preset_name])
        return snap

    def _foreground_busy(self):
        if getattr(self, "_batch_active", False):
            return True
        for name in (
            "download_worker",
            "_fetch_worker",
            "_batch_fetch_worker",
            "_auto_record_resolve_worker",
            "_expand_worker",
            "_scan_worker",
        ):
            worker = getattr(self, name, None)
            if worker is not None and worker.isRunning():
                return True
        return False

    def _resolve_history_url(self):
        if self._active_history_url:
            return self._active_history_url
        req = getattr(self, "_last_fetch_request", {})
        vod_source = req.get("vod_source", "")
        if vod_source:
            return vod_source
        if self._queue_active_item is not None:
            return self._queue_active_item.get("url", "")
        if hasattr(self, "url_input"):
            return self.url_input.text().strip()
        return ""

    def _choose_default_quality_index(self, qualities, platform):
        """Resolve the user's per-platform default to an index into the
        given quality list. Fallbacks:

          pref "1080p" -> first quality whose name/resolution contains "1080"
          pref "720p"  -> first 720
          pref "source"/"highest"/"" -> 1080-or-source heuristic (legacy)
          pref "lowest"  -> last (qualities are typically sorted high→low)
        """
        prefs = self._config.get("quality_defaults") or {}
        pkey = (platform or "").strip().lower() or "other"
        pref = (prefs.get(pkey) or prefs.get("other") or "").strip().lower()
        if not pref or pref in ("highest", "source"):
            # Legacy behaviour: prefer 1080 or "source" if present, else 0.
            for i, q in enumerate(qualities):
                if "1080" in q.name or "source" in q.name.lower():
                    return i
            return 0
        if pref == "lowest":
            return len(qualities) - 1
        for i, q in enumerate(qualities):
            cname = (q.name or "").lower()
            cres = (q.resolution or "").lower()
            if pref in cname or pref in cres:
                return i
        # No match — fall back to the legacy heuristic.
        for i, q in enumerate(qualities):
            if "1080" in q.name or "source" in q.name.lower():
                return i
        return 0

    def _preflight_disk_space(self):
        """Show a confirm dialog when the estimated download size is more
        than 80% of free space. Returns True if the user wants to proceed
        (or the check is inapplicable), False to abort.

        Lives and unknown-duration streams pass through silently since we
        can't estimate them meaningfully.
        """
        try:
            out_dir = self.output_input.text().strip() or str(_default_output_dir())
            free = _free_space_bytes(out_dir)
            estimate = _estimate_download_bytes(self.stream_info)
            if not free or not estimate or estimate <= 0:
                return True
            if estimate <= free * 0.8:
                return True
            return ask_premium_confirmation(
                self,
                title="Low free space on the output drive",
                body=(
                    f"This download may need about {_fmt_size(estimate)}, but only "
                    f"{_fmt_size(free)} is currently free in the output location."
                ),
                eyebrow="PREFLIGHT",
                badge_text="Capacity risk",
                tone="warning",
                summary_title="Continuing could leave you with a partial or failed download.",
                summary_body="Free up space first if you want the safest path.",
                details_title="Capacity estimate",
                details_body=(
                    f"Estimated download size: {_fmt_size(estimate)}\n"
                    f"Free space available: {_fmt_size(free)}\n"
                    f"Output folder: {out_dir}"
                ),
                primary_label="Continue anyway",
                secondary_label="Cancel",
                default_action="secondary",
                min_width=620,
            )
        except Exception as e:
            self._log(f"[PREFLIGHT] disk-space check failed: {e}")
            return True


    # ── Queue status helpers ────────────────────────────────────

    def _queue_status_changed(self):
        self._persist_config()
        if hasattr(self, "queue_table"):
            self._refresh_queue_table()

    def _set_queue_item_status(self, item, status, note=""):
        if item is None:
            return
        item["status"] = status
        item["note"] = note
        self._queue_status_changed()

    def _failure_resume_sidecar(self, out_dir):
        if not out_dir:
            return ""
        path = os.path.join(out_dir, ".streamkeep_resume.json")
        return path if os.path.isfile(path) else ""

    def _record_failed_job(
        self,
        *,
        stage,
        error,
        item=None,
        info=None,
        out_dir="",
        queue_data=None,
    ):
        """Persist one failed fetch/download/finalize job for recovery."""
        item = item or {}
        queue_seed = dict(queue_data or {})
        info = info or getattr(self, "_active_stream_info", None) or self.stream_info
        source_url = (
            item.get("url")
            or item.get("vod_source")
            or queue_seed.get("url")
            or queue_seed.get("vod_source")
            or self._resolve_history_url()
            or (self.url_input.text().strip() if hasattr(self, "url_input") else "")
        )
        platform = item.get("platform") or queue_seed.get("platform") or (info.platform if info else "")
        title = item.get("title") or queue_seed.get("title") or (info.title if info else "") or source_url
        output_dir = out_dir or getattr(self, "_active_output_dir", "") or (
            self.output_input.text().strip() if hasattr(self, "output_input") else ""
        )
        q_data = dict(queue_seed or item or {})
        if source_url and "url" not in q_data:
            q_data["url"] = source_url
        if title and "title" not in q_data:
            q_data["title"] = title
        if platform and "platform" not in q_data:
            q_data["platform"] = platform
        try:
            job_id = _db.save_failed_job(
                url=source_url,
                platform=platform,
                title=title,
                stage=stage,
                error=str(error or ""),
                output_dir=output_dir,
                resume_sidecar=self._failure_resume_sidecar(output_dir),
                queue_data=q_data,
                context={
                    "quality_name": getattr(self, "_active_quality_name", ""),
                    "completed_segments": getattr(self, "_completed_segments", 0),
                    "total_segments": getattr(self, "_total_segments", 0),
                },
            )
            if item is not None and job_id:
                item["failure_id"] = job_id
            return job_id
        except Exception as e:
            self._log(f"[RECOVERY] Could not save failed-job record: {e}")
            return 0

    def _retry_failed_job(self, job_id):
        job = _db.load_failed_job(job_id)
        if job and job.get("status") != "retrying":
            job = _db.mark_failed_job_retrying(job_id)
        if not job:
            self._set_status("Failed-job record was not found.", "warning")
            return False
        queue_data = dict(job.get("queue_data") or {})
        queue_data["status"] = "queued"
        queue_data["note"] = f"retry #{job.get('retry_count', 0)}"
        queue_data["failure_id"] = int(job.get("id", 0) or 0)
        if not queue_data.get("url"):
            queue_data["url"] = job.get("url", "")
        if not queue_data.get("title"):
            queue_data["title"] = job.get("title", "") or job.get("url", "")
        if not queue_data.get("platform"):
            queue_data["platform"] = job.get("platform", "") or "?"
        normalized = self._normalize_queue_item(queue_data)
        if normalized is None:
            self._set_status("Failed job has no retryable URL.", "warning")
            return False
        normalized["failure_id"] = int(job.get("id", 0) or 0)
        existing = None
        for q in self._download_queue:
            if int(q.get("failure_id", 0) or 0) == normalized["failure_id"]:
                existing = q
                break
        if existing is None:
            self._download_queue.append(normalized)
        else:
            existing.update(normalized)
        self._queue_status_changed()
        self._set_status(f"Retry queued: {normalized.get('title', '')[:60]}", "success")
        self._advance_queue()
        return True

    def _discard_failed_job(self, job_id):
        _db.mark_failed_job_discarded(job_id)
        removed = 0
        kept = []
        for q in self._download_queue:
            if int(q.get("failure_id", 0) or 0) == int(job_id) and q.get("status") == "failed":
                removed += 1
                continue
            kept.append(q)
        if removed:
            self._download_queue = kept
            self._queue_status_changed()
        self._set_status("Failed-job recovery item discarded.", "success")

    def _release_queue_item(self, status=None, note=""):
        item = self._queue_active_item
        self._queue_active_item = None
        self._queue_autostart = False
        if item is None:
            return
        if status == "done":
            # Recurring items re-schedule themselves for the next window
            # instead of being removed — this is what turns the queue into
            # "DVR my weekly show" instead of "one-off".
            recurrence = (item.get("recurrence") or "").strip().lower()
            next_fire = self._compute_next_fire(recurrence, item.get("start_at", ""))
            if next_fire:
                item["status"] = "queued"
                item["start_at"] = next_fire.isoformat(timespec="minutes")
                item["note"] = f"recurring ({recurrence}) — next fire {next_fire.strftime('%a %H:%M')}"
                self._log(
                    f"[QUEUE] Recurring item rescheduled for "
                    f"{next_fire.strftime('%Y-%m-%d %H:%M')}: "
                    f"{(item.get('title') or item.get('url', ''))[:60]}"
                )
                self._queue_status_changed()
                return
            try:
                self._download_queue.remove(item)
            except ValueError:
                pass
            self._queue_status_changed()
            return
        if status:
            self._set_queue_item_status(item, status, note)

    def _compute_next_fire(self, recurrence, start_at_iso):
        """Return the datetime of the next fire for a recurring queue
        item, or None for one-shot / unparseable recurrence strings.

        Accepted shapes (case-insensitive):
          "daily"          every 24h from last fire
          "weekly"         every 7 days from last fire
          "mon,wed,fri"    next occurrence on one of the named days
        Days use first 3 letters; any subset of mon/tue/wed/thu/fri/sat/sun.
        """
        if not recurrence:
            return None
        try:
            last = datetime.fromisoformat(start_at_iso) if start_at_iso else datetime.now()
        except Exception:
            last = datetime.now()
        now = datetime.now()
        # If the last fire was in the past we pivot off "now" so we never
        # backfill missed windows.
        base = max(last, now)
        if recurrence == "daily":
            return base + timedelta(days=1)
        if recurrence == "weekly":
            return base + timedelta(days=7)
        day_map = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
        wanted = []
        for part in recurrence.replace(" ", "").split(","):
            if part in day_map:
                wanted.append(day_map[part])
        if not wanted:
            return None
        wanted.sort()
        # Keep the wall-clock time of day from the original start_at.
        target_time = last.time() if start_at_iso else now.time()
        today_wd = now.weekday()
        for offset in range(1, 8):
            candidate_wd = (today_wd + offset) % 7
            if candidate_wd in wanted:
                target_date = now.date() + timedelta(days=offset)
                return datetime.combine(target_date, target_time)
        return None
