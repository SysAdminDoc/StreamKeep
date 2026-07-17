"""VOD listing, paging, selection, and batch handlers for Download."""

import os

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QCheckBox, QHBoxLayout, QTableWidgetItem, QWidget

from ...extractors import YtDlpExtractor
from ...theme import CAT
from ...utils import (
    build_template_context as _build_template_context,
    render_template as _render_template,
    safe_filename as _safe_filename,
)
from ...workers import DownloadWorker, FetchWorker, VodPageWorker
from ..widgets import PLATFORM_BADGES


class DownloadVodMixin:
    """Paginated VOD discovery and bounded batch download orchestration."""

    def _refresh_vod_summary(self):
        if not hasattr(self, "vod_summary_label"):
            return
        total = len(self._vod_checks)
        checked_indices = [i for i, cb in enumerate(self._vod_checks) if cb.isChecked()]
        checked = len(checked_indices)
        if total and checked:
            # Sum duration of selected VODs
            total_ms = 0
            for i in checked_indices:
                if i < len(self._vod_list):
                    total_ms += self._vod_list[i].duration_ms or 0
            dur_str = ""
            if total_ms > 0:
                secs = total_ms // 1000
                h, m = divmod(secs // 60, 60)
                dur_str = f" · {h}h {m}m" if h else f" · {m}m"
            self.vod_summary_label.setText(f"{checked} of {total} selected{dur_str}")
        elif total:
            self.vod_summary_label.setText(f"0 of {total} selected")
        else:
            self.vod_summary_label.setText("Inspect a channel to browse available VODs.")

    def _on_vods_found(self, vod_list, platform_name, next_cursor=None):
        self._vod_next_cursor = next_cursor
        self._vod_source_url = self.url_input.text().strip()
        if self._queue_autostart and self._queue_active_item is not None:
            added = 0
            for vod in vod_list:
                if self._queue_add(
                        vod.source,
                        title=vod.title,
                        platform=vod.platform or platform_name,
                        vod_source=vod.source,
                        vod_platform=vod.platform or platform_name,
                        vod_title=vod.title,
                        vod_channel=vod.channel):
                    added += 1
            source_label = self._queue_active_item.get("title") or self._queue_active_item.get("url", "")
            self._release_queue_item("done")
            self._log(f"[QUEUE] Expanded {source_label[:60]} into {added} queued VOD(s)")
            tone = "success" if added else "warning"
            self._set_status(
                f"Expanded queued source into {added} VOD(s).",
                tone,
            )
            self._start_next_background_job()
            return

        self._vod_list = vod_list
        self._vod_checks = []
        self.vod_table.setRowCount(len(vod_list))
        self._update_badge(platform_name)

        for i, v in enumerate(vod_list):
            cb = QCheckBox()
            cb.setAccessibleName(f"Select VOD {i + 1}: {v.title}")
            cb.stateChanged.connect(lambda _state, row=i: self._on_vod_cb_toggled(row))
            cb_widget = QWidget()
            cb_lay = QHBoxLayout(cb_widget)
            cb_lay.addWidget(cb)
            cb_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cb_lay.setContentsMargins(0, 0, 0, 0)
            self.vod_table.setCellWidget(i, 0, cb_widget)
            self._vod_checks.append(cb)

            # Platform badge
            badge = PLATFORM_BADGES.get(v.platform, {})
            plat_item = QTableWidgetItem(v.platform)
            plat_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if badge.get("color"):
                plat_item.setForeground(QColor(badge["color"]))
            self.vod_table.setItem(i, 1, plat_item)

            # Title
            live = " [LIVE]" if v.is_live else ""
            title_item = QTableWidgetItem(f"{v.title}{live}")
            if v.is_live:
                title_item.setForeground(QColor(CAT["green"]))
            self.vod_table.setItem(i, 2, title_item)

            date_item = QTableWidgetItem(v.date)
            date_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.vod_table.setItem(i, 3, date_item)

            dur_item = QTableWidgetItem(v.duration if v.duration else "Live")
            dur_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.vod_table.setItem(i, 4, dur_item)

            views_str = f"{v.viewers:,}" if v.viewers else "—"
            views_item = QTableWidgetItem(views_str)
            views_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.vod_table.setItem(i, 5, views_item)

        self._vod_last_checked_row = -1  # shift-click anchor
        self.vod_widget.setVisible(True)
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.setText("Resolve")
        self._refresh_vod_summary()
        if hasattr(self, "vod_load_more_btn"):
            self.vod_load_more_btn.setVisible(bool(next_cursor))
            self.vod_load_more_btn.setEnabled(bool(next_cursor))
            self.vod_load_more_btn.setText("Load More VODs")
        self._set_status(f"Found {len(vod_list)} VOD(s). Select one to inspect or batch download.", "success")

    def _on_vod_cb_toggled(self, row):
        """Handle a VOD checkbox toggle — supports shift-click range select."""
        from PyQt6.QtWidgets import QApplication
        modifiers = QApplication.keyboardModifiers()
        anchor = getattr(self, "_vod_last_checked_row", -1)
        if modifiers & Qt.KeyboardModifier.ShiftModifier and 0 <= anchor < len(self._vod_checks):
            lo, hi = min(anchor, row), max(anchor, row)
            target_state = self._vod_checks[row].isChecked()
            for r in range(lo, hi + 1):
                if r < len(self._vod_checks):
                    self._vod_checks[r].blockSignals(True)
                    self._vod_checks[r].setChecked(target_state)
                    self._vod_checks[r].blockSignals(False)
        self._vod_last_checked_row = row
        self._refresh_vod_summary()

    def _on_vod_select_all(self, state):
        checked = state == Qt.CheckState.Checked.value
        for cb in self._vod_checks:
            cb.setChecked(checked)
        self._refresh_vod_summary()

    def _on_vod_queue_selected(self):
        """Add all checked VODs to the download queue."""
        checked = [self._vod_list[i] for i, cb in enumerate(self._vod_checks) if cb.isChecked()]
        if not checked:
            self._set_status("Select at least one VOD to queue.", "warning")
            return
        added = 0
        for vod in checked:
            ok = self._queue_add(
                vod.source,
                title=vod.title,
                platform=vod.platform,
                vod_source=vod.source,
                vod_platform=vod.platform,
                vod_title=vod.title,
                vod_channel=vod.channel,
            )
            if ok:
                added += 1
        self._log(f"[QUEUE] Added {added} of {len(checked)} checked VOD(s) to queue")
        self._set_status(
            f"Queued {added} VOD(s) for download." if added else "All selected VODs are already queued.",
            "success" if added else "info",
        )
        if added:
            self._advance_queue()

    def _on_vod_load_single(self):
        for i, cb in enumerate(self._vod_checks):
            if cb.isChecked():
                vod = self._vod_list[i]
                self._log(f"\nLoading VOD: {vod.title} ({vod.date})")
                self._on_fetch(
                    vod_source=vod.source,
                    vod_platform=vod.platform,
                    vod_title=vod.title,
                    vod_channel=vod.channel,
                )
                return
        self._log("No VOD checked.")
        self._set_status("Select at least one VOD before loading it.", "warning")

    def _on_vod_load_more(self):
        """Fetch the next page of VODs and append to the table."""
        cursor = getattr(self, "_vod_next_cursor", None)
        url = getattr(self, "_vod_source_url", "")
        if not cursor or not url:
            return
        # Cancel any previous page worker
        pw = getattr(self, "_vod_page_worker", None)
        if pw is not None and pw.isRunning():
            pw.requestInterruption()
            pw.wait(1500)
        if hasattr(self, "vod_load_more_btn"):
            self.vod_load_more_btn.setEnabled(False)
            self.vod_load_more_btn.setText("Loading…")
        self._log(f"[VOD] Fetching next page (cursor: {str(cursor)[:20]}…)")
        worker = VodPageWorker(url, cursor)
        worker.log.connect(self._log)
        worker.page_ready.connect(self._on_vod_page_ready)
        worker.error.connect(self._on_vod_page_error)
        self._vod_page_worker = worker
        worker.start()

    def _on_vod_page_ready(self, new_vods, next_cursor):
        """Append a page of VODs to the existing table."""
        self._vod_next_cursor = next_cursor
        if not new_vods:
            self._log("[VOD] No more VODs on the next page.")
            self._set_status("No additional VODs found.", "info")
            if hasattr(self, "vod_load_more_btn"):
                self.vod_load_more_btn.setVisible(False)
            return
        # Append to the existing list
        start_row = len(self._vod_list)
        self._vod_list.extend(new_vods)
        self.vod_table.setRowCount(len(self._vod_list))
        for i, v in enumerate(new_vods, start=start_row):
            cb = QCheckBox()
            cb.setAccessibleName(f"Select VOD {i + 1}: {v.title}")
            cb.stateChanged.connect(lambda _state, row=i: self._on_vod_cb_toggled(row))
            cb_widget = QWidget()
            cb_lay = QHBoxLayout(cb_widget)
            cb_lay.addWidget(cb)
            cb_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cb_lay.setContentsMargins(0, 0, 0, 0)
            self.vod_table.setCellWidget(i, 0, cb_widget)
            self._vod_checks.append(cb)
            badge = PLATFORM_BADGES.get(v.platform, {})
            plat_item = QTableWidgetItem(v.platform)
            plat_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if badge.get("color"):
                plat_item.setForeground(QColor(badge["color"]))
            self.vod_table.setItem(i, 1, plat_item)
            live = " [LIVE]" if v.is_live else ""
            title_item = QTableWidgetItem(f"{v.title}{live}")
            if v.is_live:
                title_item.setForeground(QColor(CAT["green"]))
            self.vod_table.setItem(i, 2, title_item)
            date_item = QTableWidgetItem(v.date)
            date_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.vod_table.setItem(i, 3, date_item)
            dur_item = QTableWidgetItem(v.duration if v.duration else "Live")
            dur_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.vod_table.setItem(i, 4, dur_item)
            views_str = f"{v.viewers:,}" if v.viewers else "—"
            views_item = QTableWidgetItem(views_str)
            views_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.vod_table.setItem(i, 5, views_item)
        self._refresh_vod_summary()
        if hasattr(self, "vod_load_more_btn"):
            self.vod_load_more_btn.setVisible(bool(next_cursor))
            self.vod_load_more_btn.setEnabled(bool(next_cursor))
            self.vod_load_more_btn.setText("Load More VODs")
        total = len(self._vod_list)
        self._log(f"[VOD] Loaded {len(new_vods)} more — {total} total")
        self._set_status(f"{total} VOD(s) loaded. {len(new_vods)} new from this page.", "success")

    def _on_vod_page_error(self, err):
        self._log(f"[VOD] Pagination error: {err}")
        self._set_status(f"Failed to load more VODs: {err}", "error")
        if hasattr(self, "vod_load_more_btn"):
            self.vod_load_more_btn.setEnabled(True)
            self.vod_load_more_btn.setText("Load More VODs")

    def _on_vod_download_all(self):
        checked = [self._vod_list[i] for i, cb in enumerate(self._vod_checks) if cb.isChecked()]
        if not checked:
            self._log("No VODs checked.")
            self._set_status("Select at least one VOD before starting a batch download.", "warning")
            return

        self._cancel_batch_fetch_worker()
        self._batch_vods = checked
        self._batch_idx = 0
        self._batch_total = len(checked)
        self._batch_failed_count = 0
        self._batch_active = True
        self._log(f"\n{'=' * 50}")
        self._log(f"Batch downloading {self._batch_total} VOD(s)")
        self._log(f"{'=' * 50}")
        self.download_btn.setEnabled(False)
        self.fetch_btn.setEnabled(False)
        self.vod_dl_all_btn.setEnabled(False)
        self.vod_load_btn.setEnabled(False)
        self.stop_btn.setVisible(True)
        self._set_status(f"Batch download queued for {self._batch_total} VOD(s).", "working")
        self._batch_next()


    # ── Batch VOD download ──────────────────────────────────────

    def _batch_next(self):
        if not self._batch_active:
            return
        if self._batch_idx >= self._batch_total:
            self._batch_done()
            return
        vod = self._batch_vods[self._batch_idx]
        self._log(f"\n--- VOD {self._batch_idx + 1}/{self._batch_total}: {vod.title} ---")
        self._set_status(
            f"Preparing VOD {self._batch_idx + 1} of {self._batch_total}: {vod.title}",
            "working",
        )

        worker = FetchWorker(
            self.url_input.text().strip(),
            vod_source=vod.source,
            vod_platform=vod.platform,
            vod_title=vod.title,
            vod_channel=vod.channel,
        )
        worker.log.connect(self._log)
        worker.finished.connect(self._batch_on_fetched)
        worker.error.connect(self._batch_on_fetch_error)
        self._batch_fetch_worker = worker
        worker.start()

    def _batch_on_fetched(self, info):
        self._batch_fetch_worker = None
        if not self._batch_active or self._batch_idx >= self._batch_total:
            return
        vod = self._batch_vods[self._batch_idx]

        # Pick quality (prefer 1080p/source)
        playlist_url = None
        fmt_type = "hls"
        audio_url = ""
        ytdlp_source = ""
        ytdlp_format = ""
        selected_q = None
        for q in info.qualities:
            if "1080" in q.name or "source" in q.name.lower():
                selected_q = q
                break
        if not selected_q and info.qualities:
            selected_q = info.qualities[0]
        if selected_q:
            playlist_url = selected_q.url
            fmt_type = selected_q.format_type
            audio_url = selected_q.audio_url
            ytdlp_source = selected_q.ytdlp_source
            ytdlp_format = selected_q.ytdlp_format

        if not playlist_url and not ytdlp_source:
            self._log(f"[ERROR] No playback URL for {vod.title}")
            self._batch_idx += 1
            self._batch_next()
            return

        total_secs = info.total_secs
        seg_secs = self._get_segment_secs()

        # Render folder + filename from templates
        ctx = _build_template_context(info, vod)
        folder_parts = _render_template(
            self._folder_template, ctx
        ) or [_safe_filename(vod.title) or f"{info.platform}_download"]
        file_parts = _render_template(self._file_template, ctx)
        title_safe = file_parts[-1] if file_parts else (
            _safe_filename(info.title) or _safe_filename(vod.title) or f"{info.platform}_download"
        )

        # ytdlp_direct and mp4 formats download monolithically — no segment splitting
        if fmt_type in ("ytdlp_direct", "mp4") or seg_secs == 0 or total_secs <= 0 or total_secs <= seg_secs:
            segments = [(0, title_safe, 0, int(total_secs) if total_secs > 0 else 0)]
        else:
            segments = []
            pos, idx = 0, 0
            while pos < total_secs:
                end = min(pos + seg_secs, total_secs)
                segments.append((idx, f"{title_safe}_part{idx + 1:02d}", pos, int(end - pos)))
                pos = end
                idx += 1

        out_dir = os.path.join(self.output_input.text().strip(), *folder_parts)
        os.makedirs(out_dir, exist_ok=True)

        self._build_segments(total_secs)
        self.stream_info = info

        self._total_segments = len(segments)
        self._completed_segments = 0
        self._download_had_errors = False
        self.overall_progress.setVisible(True)
        self.overall_progress.setValue(0)
        self.overall_progress.setMaximum(len(segments))
        self._refresh_download_summary()
        self._set_status(
            f"Downloading VOD {self._batch_idx + 1} of {self._batch_total}.",
            "working",
        )
        self._set_download_context(
            out_dir=out_dir,
            quality_name=selected_q.name if selected_q else "batch",
            history_url=vod.source or self.url_input.text().strip(),
            info=info,
        )

        worker = DownloadWorker(playlist_url or "", segments, out_dir, format_type=fmt_type)
        worker.audio_url = audio_url
        if selected_q:
            from ...models import default_media_tracks
            worker.selected_tracks = default_media_tracks(selected_q)
        worker.ytdlp_source = ytdlp_source
        worker.ytdlp_format = ytdlp_format
        worker.cookies_browser = YtDlpExtractor.cookies_browser
        worker.rate_limit = YtDlpExtractor.rate_limit
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
            apply_external_downloader_options, apply_ytdlp_transfer_options,
        )
        apply_ytdlp_transfer_options(worker, YtDlpExtractor)
        apply_external_downloader_options(worker, YtDlpExtractor)
        worker.parallel_connections = self._parallel_connections
        worker.progress.connect(self._on_dl_progress)
        worker.segment_done.connect(self._on_segment_done)
        worker.error.connect(self._on_dl_error)
        worker.log.connect(self._log)
        worker.all_done.connect(self._batch_vod_done)
        self.download_worker = worker
        self._attach_resume_to_worker(worker)
        worker.start()

    def _batch_on_fetch_error(self, err):
        self._batch_fetch_worker = None
        if not self._batch_active or self._batch_idx >= self._batch_total:
            return
        self._log(f"[ERROR] {err}")
        self._set_status(f"Batch fetch error: {err}", "error")
        if hasattr(self, "_batch_failed_count"):
            self._batch_failed_count += 1
        self._batch_idx += 1
        self._batch_next()

    def _batch_vod_done(self):
        if not self._batch_active or self._batch_idx >= self._batch_total:
            return
        vod = self._batch_vods[self._batch_idx]
        if self._download_had_errors:
            self._log(f"[WARN] {vod.title} finished with errors")
            if hasattr(self, "_batch_failed_count"):
                self._batch_failed_count += 1
        else:
            self._log(f"[DONE] {vod.title}")
            self._save_metadata(
                self._active_output_dir,
                self._active_quality_name or "batch",
                history_url=self._active_history_url or vod.source,
                info=self._active_stream_info or self.stream_info,
            )
        self._batch_idx += 1
        self._batch_next()

    def _batch_done(self):
        self._batch_active = False
        self._batch_fetch_worker = None
        failed = getattr(self, "_batch_failed_count", 0)
        done = self._batch_total - failed
        self._log(f"\n{'=' * 50}")
        if failed:
            self._log(
                f"Batch finished with {failed} failed VOD(s). Completed {done} of {self._batch_total}."
            )
        else:
            self._log(f"Batch complete! {self._batch_total} VOD(s) downloaded.")
        self._log(f"{'=' * 50}")
        if failed:
            self._set_status(
                f"Batch finished with {failed} failed VOD(s). Completed {done} of {self._batch_total}.",
                "warning",
            )
        else:
            self._set_status(f"Batch complete. Downloaded {self._batch_total} VOD(s).", "success")
            self._notify("StreamKeep — Batch complete", f"Downloaded {self._batch_total} VOD(s)")
            self._send_webhook("batch complete", f"{self._batch_total} VODs",
                               "Batch download finished")
        self.download_btn.setEnabled(True)
        self.fetch_btn.setEnabled(True)
        self.vod_dl_all_btn.setEnabled(True)
        self.vod_load_btn.setEnabled(True)
        self.stop_btn.setVisible(False)
        self.open_folder_btn.setVisible(True)
        self._persist_config()

    def _cancel_batch_fetch_worker(self):
        worker = getattr(self, "_batch_fetch_worker", None)
        if worker is None:
            return
        try:
            worker.requestInterruption()
        except Exception:
            pass
        if worker.isRunning():
            try:
                worker.wait(1500)
            except Exception:
                pass
        self._batch_fetch_worker = None
