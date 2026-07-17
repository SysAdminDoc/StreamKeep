"""Single fetch/download, segment, playlist, and scan handlers."""

import os
import re
import time
from collections import deque
from datetime import datetime

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QColor, QDesktopServices
from PyQt6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QProgressBar,
    QTableWidgetItem,
    QWidget,
)

from ... import db as _db
from ...extractors import Extractor, YtDlpExtractor
from ...theme import CAT
from ...utils import (
    build_template_context as _build_template_context,
    default_output_dir as _default_output_dir,
    fmt_duration as _fmt_duration,
    fmt_size as _fmt_size,
    free_space_bytes as _free_space_bytes,
    render_template as _render_template,
    safe_filename as _safe_filename,
)
from ...workers import (
    DownloadWorker,
    FetchWorker,
    PageScrapeWorker as _PageScrapeWorker,
    PlaylistExpandWorker as _PlaylistExpandWorker,
)
from ..widgets import (
    PLATFORM_BADGES,
    ask_premium_confirmation,
    path_label as _path_label,
)
from .download_controls import (
    _populate_adv_subtitles,
    _populate_track_table,
    _reset_adv_overrides,
    get_adv_overrides,
    get_selected_media_tracks,
)


class DownloadSingleMixin:
    """One active fetch/download and its direct supporting workflows."""

    def _refresh_download_summary(self):
        if not hasattr(self, "download_hero_title"):
            return

        url = self.url_input.text().strip() if hasattr(self, "url_input") else ""
        if self.stream_info:
            title = self.stream_info.title or "Ready to download"
            summary_parts = []
            if self.stream_info.platform:
                summary_parts.append(self.stream_info.platform)
            if self.stream_info.channel:
                summary_parts.append(self.stream_info.channel)
            if self.stream_info.duration_str:
                summary_parts.append(self.stream_info.duration_str)
            if self.stream_info.is_live:
                summary_parts.append("Live capture")
            body = "  •  ".join(summary_parts) if summary_parts else "Metadata loaded."
        elif url:
            ext = Extractor.detect(url)
            title = "Source detected" if ext else "New download"
            if ext:
                body = f"{ext.NAME} link recognized. Fetch when ready."
            else:
                body = "Paste a supported stream, VOD, podcast, or media URL."
        else:
            title = "New download"
            body = "Paste a stream, VOD, podcast, or media URL."

        self.download_hero_title.setText(title)
        self.download_hero_body.setText(body)

        platform_value = self.stream_info.platform if self.stream_info else "Auto detect"
        platform_sub = "Detected after fetch" if self.stream_info else "Waiting for a supported URL"
        duration_value = self.stream_info.duration_str if self.stream_info and self.stream_info.duration_str else "Waiting"
        duration_sub = "Stream length" if self.stream_info else "Metadata not loaded yet"

        total_segments = len(self._segment_checks)
        checked_segments = sum(1 for cb in self._segment_checks if cb.isChecked())
        if total_segments:
            selection_value = f"{checked_segments}/{total_segments}"
            selection_sub = "segments selected"
        elif self.stream_info and self.stream_info.total_secs <= 0:
            selection_value = "Live"
            selection_sub = "capture runs until you stop it"
        else:
            selection_value = "Not ready"
            selection_sub = "segments appear after fetch"

        output_path = self.output_input.text().strip() if hasattr(self, "output_input") else ""
        output_sub = output_path if len(output_path) <= 50 else f"...{output_path[-47:]}"
        finalize_active = bool(self._finalize_worker is not None and self._finalize_worker.isRunning())
        finalize_queued = len(self._finalize_tasks)
        if finalize_active:
            if self._finalize_active_total:
                finalize_value = f"{self._finalize_active_step}/{self._finalize_active_total}"
            else:
                finalize_value = "Starting"
            finalize_parts = []
            if self._finalize_active_title:
                finalize_parts.append(self._finalize_active_title[:42])
            if self._finalize_active_label:
                finalize_parts.append(self._finalize_active_label)
            finalize_sub = " | ".join(finalize_parts) or "Preparing background cleanup"
            if finalize_queued:
                finalize_sub = f"{finalize_sub} | {finalize_queued} queued"
        elif finalize_queued:
            finalize_value = f"{finalize_queued} queued"
            finalize_sub = "Waiting for the current background cleanup"
        else:
            finalize_value = "Idle"
            finalize_sub = "Metadata and post-processing will queue here"

        self._set_metric(self.download_platform_value, self.download_platform_sub, platform_value, platform_sub)
        self._set_metric(self.download_duration_value, self.download_duration_sub, duration_value, duration_sub)
        self._set_metric(self.download_selection_value, self.download_selection_sub, selection_value, selection_sub)
        # Append free-disk hint to the output card subline so users see
        # what they have to work with before starting a long download.
        free_bytes = _free_space_bytes(output_path) if output_path else None
        free_label = f"{_fmt_size(free_bytes)} free" if free_bytes else ""
        output_sub_with_free = output_sub or "Choose a destination folder"
        if free_label:
            output_sub_with_free = f"{free_label} \u2022 {output_sub_with_free}"
        self._set_metric(
            self.download_output_value,
            self.download_output_sub,
            _path_label(output_path),
            output_sub_with_free,
        )
        if hasattr(self, "download_finalize_value"):
            self._set_metric(
                self.download_finalize_value,
                self.download_finalize_sub,
                finalize_value,
                finalize_sub,
            )
        self.download_output_value.setToolTip(output_path)
        self.download_output_sub.setToolTip(output_path)
        if hasattr(self, "download_finalize_sub"):
            self.download_finalize_sub.setToolTip(finalize_sub)

        if hasattr(self, "segment_summary_label"):
            if total_segments:
                self.segment_summary_label.setText(f"{checked_segments} of {total_segments} segment(s) selected")
            else:
                self.segment_summary_label.setText("Segments will appear after metadata is loaded.")


    # Monitor tab handlers → streamkeep.ui.tabs.monitor.MonitorTabMixin

    # History tab handlers → streamkeep.ui.tabs.history.HistoryTabMixin

    def _update_badge(self, platform_name=None):
        if platform_name and platform_name in PLATFORM_BADGES:
            badge = PLATFORM_BADGES[platform_name]
            self.platform_badge.setText(f" {badge['text']} ")
            self.platform_badge.setStyleSheet(
                f"background-color: {badge['color']}; color: {CAT['crust']}; "
                f"border-radius: 4px; font-weight: 600; font-size: 12px; padding: 3px 8px;"
            )
            self.platform_badge.setVisible(True)
        else:
            self.platform_badge.setVisible(False)


    # ── URL input / clipboard ───────────────────────────────────

    def _on_url_changed(self, text):
        ext = Extractor.detect(text.strip())
        if ext:
            self._update_badge(ext.NAME)
            ch = ext.extract_channel_id(text.strip())
            if ch and self._can_autofill_output():
                self._apply_auto_output(str(_default_output_dir() / _safe_filename(ch)))
        else:
            self._update_badge(None)
        self._refresh_download_summary()

    def _on_toggle_clipboard(self, checked):
        if checked:
            self.clipboard_monitor.start()
            self._log("[CLIPBOARD] Monitoring started - copy a URL to auto-load")
            self._set_status("Clipboard monitoring active. Copy a supported URL to load it automatically.", "working")
        else:
            self.clipboard_monitor.stop()
            self._log("[CLIPBOARD] Monitoring stopped")
            self._set_status("Clipboard monitoring stopped.", "idle")

    def _on_clipboard_url(self, url):
        # Don't interrupt an active download
        existing = getattr(self, "download_worker", None)
        if existing is not None and existing.isRunning():
            self._log(f"[CLIPBOARD] Ignored {url[:60]}... (download in progress)")
            return
        # Basic URL sanity — reject newlines/control chars
        if "\n" in url or "\r" in url or len(url) > 2048:
            self._log("[CLIPBOARD] Rejected malformed URL")
            return
        # Dedup: ignore if already in the input box (avoids re-fetching on focus switches)
        if url == self.url_input.text().strip():
            return
        # Dedup: ignore if it's the same as the last clipboard URL we accepted
        if url == getattr(self, "_last_clipboard_url", ""):
            return
        self._last_clipboard_url = url
        self._log(f"[CLIPBOARD] Detected: {url}")
        self.url_input.setText(url)
        self._switch_tab(0)  # Switch to Download tab
        self._on_fetch()

    def _remember_url(self, url):
        """Add URL to the top of the recent URLs list (most-recent-first)."""
        if not url:
            return
        previous = list(self._recent_urls)
        # Dedup: move to front if already present
        if url in self._recent_urls:
            self._recent_urls.remove(url)
        self._recent_urls.insert(0, url)
        # Keep the last 30
        self._recent_urls = self._recent_urls[:30]
        if hasattr(self, "_recent_url_model"):
            self._recent_url_model.setStringList(self._recent_urls)
        if self._recent_urls != previous:
            self._schedule_persist_config()


    # ── Fetch / resolve ─────────────────────────────────────────

    def _on_fetch(self, vod_source=None, vod_platform=None, vod_title=None, vod_channel=None):
        url = self.url_input.text().strip()
        if not url:
            return
        self._last_fetch_request = {
            "url": url,
            "vod_source": vod_source or "",
            "vod_platform": vod_platform or "",
            "vod_title": vod_title or "",
            "vod_channel": vod_channel or "",
        }
        # Track recent URLs for the autocomplete dropdown
        if not vod_source:
            self._remember_url(url)
        # Check for URL-based duplicate before hitting the network
        if not vod_source:
            dup = self._find_duplicate(url)
            if dup:
                self._log(f"[DUPLICATE] Already downloaded on {dup.date} to {dup.path}")
                self._set_status(
                    f"Already downloaded {dup.date} to {dup.path}. Fetching anyway.",
                    "warning",
                )
        self.fetch_btn.setEnabled(False)
        self.fetch_btn.setText("Resolving")
        self.download_btn.setEnabled(False)
        self.open_folder_btn.setVisible(False)
        if hasattr(self, "trim_btn"):
            self.trim_btn.setVisible(False)
        self.overall_progress.setVisible(False)
        self.quality_combo.clear()
        self.quality_combo.setEnabled(False)
        _populate_adv_subtitles(self, None)
        self.table.setRowCount(0)
        if hasattr(self, "segments_section"):
            self.segments_section.setVisible(False)
        self._segment_checks = []
        self._segment_progress = []
        self.info_label.setVisible(False)
        self.stream_info = None
        if not vod_source:
            self.vod_widget.setVisible(False)
            self._vod_checks = []
            self._refresh_vod_summary()
        self._refresh_download_summary()
        self._set_status("Fetching stream info and available playback options...", "working")

        # Disconnect any existing fetch worker to prevent stale signals
        prev_worker = getattr(self, "_fetch_worker", None)
        if prev_worker is not None:
            try:
                prev_worker.log.disconnect()
                prev_worker.finished.disconnect()
                prev_worker.vods_found.disconnect()
                prev_worker.error.disconnect()
            except (TypeError, RuntimeError):
                pass
            if prev_worker.isRunning():
                prev_worker.requestInterruption()

        self._fetch_worker = FetchWorker(
            url,
            vod_source=vod_source,
            vod_platform=vod_platform,
            vod_title=vod_title,
            vod_channel=vod_channel,
        )
        self._fetch_worker.log.connect(self._log)
        self._fetch_worker.finished.connect(self._on_fetch_done)
        self._fetch_worker.vods_found.connect(self._on_vods_found)
        self._fetch_worker.error.connect(self._on_fetch_error)
        self._fetch_worker.start()

    def _on_fetch_done(self, info):
        if info is None:
            self._on_fetch_error("Extractor returned no stream info")
            return
        self.stream_info = info
        _populate_adv_subtitles(self, info)
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.setText("Resolve")
        self._update_badge(info.platform)

        # Populate qualities
        self.quality_combo.blockSignals(True)
        self.quality_combo.clear()
        qualities = info.qualities or []
        for q in qualities:
            bw_mbps = q.bandwidth / 1_000_000 if q.bandwidth else 0
            ft_tag = f" [{q.format_type.upper()}]" if q.format_type != "hls" else ""
            fps = getattr(q, "frame_rate", 0.0) or 0.0
            fps_tag = f" {fps:.0f}fps" if fps else ""
            vr = str(getattr(q, "video_range", "") or "").upper()
            hdr_tag = " HDR" if vr in ("PQ", "HLG") else ""
            label = (
                f"{q.name} ({q.resolution}{fps_tag}{hdr_tag}, "
                f"{bw_mbps:.1f} Mbps){ft_tag}"
            )
            self.quality_combo.addItem(label, q)
        if qualities:
            selected_idx = self._choose_default_quality_index(
                qualities, info.platform or ""
            )
            self.quality_combo.setCurrentIndex(selected_idx)
        self.quality_combo.setEnabled(len(qualities) > 0)
        self.quality_combo.blockSignals(False)
        _populate_track_table(self)
        if not qualities:
            self._log("[WARN] No playable qualities found for this URL.")

        # Stream info
        parts = [f"Platform: {info.platform}", f"Duration: {info.duration_str}"]
        if info.title:
            parts.insert(1, f"Title: {info.title[:60]}")
        if info.start_time:
            try:
                dt = datetime.fromisoformat(info.start_time.replace("Z", "+00:00"))
                parts.append(f"Started: {dt.strftime('%Y-%m-%d %I:%M %p UTC')}")
            except Exception:
                pass
        if info.segment_count:
            parts.append(f"Segments: {info.segment_count}")
        self.info_label.setText("  |  ".join(parts))
        self.info_label.setVisible(True)

        # Update output folder to use the title for non-channel content (yt-dlp, Direct, etc.)
        if info.title and info.platform in ("yt-dlp", "Direct", "Rumble", "SoundCloud",
                                            "Reddit", "Audius", "Podcast"):
            current_out = self.output_input.text().strip()
            parent = os.path.dirname(current_out)
            if parent and self._can_autofill_output():
                new_out = os.path.join(parent, _safe_filename(info.title))
                self._apply_auto_output(new_out)

        self._build_segments(info.total_secs)
        self.download_btn.setEnabled(True)
        self._refresh_download_summary()

        # Metadata-based duplicate check after resolve (F40 — fuzzy matching)
        dup = self._find_duplicate(
            "", info.title, platform=info.platform,
            duration_secs=info.total_secs,
        )
        if dup:
            self._log(f"[DUPLICATE] Match: already downloaded {dup.date} to {dup.path}")
            self._set_status(
                f"Possible duplicate of \"{dup.title}\" ({dup.date}). Download anyway if intentional.",
                "warning",
            )
            # Advisory dialog — non-blocking for queue/batch, shown for manual fetches
            if not self._queue_autostart:
                details = (
                    f"Title: {dup.title}\n"
                    f"Downloaded: {dup.date}\n"
                    f"Quality: {dup.quality}\n"
                    f"Size: {dup.size}\n"
                    f"Location: {dup.path}"
                )
                if not ask_premium_confirmation(
                    self,
                    title="Possible duplicate found",
                    body="StreamKeep found a recording in your library that closely matches what you are about to download.",
                    eyebrow="DOWNLOAD",
                    badge_text="Potential match",
                    tone="warning",
                    summary_title="Downloading again may waste storage and clutter history.",
                    summary_body="Continue only if you intentionally want another copy or a better variant.",
                    details_title="Existing recording",
                    details_body=details,
                    primary_label="Download anyway",
                    secondary_label="Skip download",
                    default_action="secondary",
                    min_width=640,
                ):
                    self._set_status("Download skipped — duplicate detected.", "idle")
                    return
        elif info.is_live or info.total_secs <= 0:
            self._set_status("Live source ready. Start recording and stop it when you have enough footage.", "success")
        else:
            self._set_status("Source ready. Review the segments and start the download when you are happy.", "success")

        if self._queue_autostart and self._queue_active_item is not None:
            self._set_queue_item_status(self._queue_active_item, "downloading")
            if not self._on_download():
                self._release_queue_item("failed", "Could not start the queued download")
                self._start_next_background_job()

    def _on_fetch_error(self, err):
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.setText("Resolve")
        self._log(f"[ERROR] {err}")
        self._record_failed_job(
            stage="fetch",
            error=err,
            item=self._queue_active_item,
            out_dir=self.output_input.text().strip() if hasattr(self, "output_input") else "",
        )
        self._refresh_download_summary()
        self._set_status(f"Fetch failed: {err}", "error")
        if self._queue_active_item is not None:
            self._release_queue_item("failed", err[:120])
            self._start_next_background_job()


    # ── VOD listing ─────────────────────────────────────────────



    # ── Segment management ──────────────────────────────────────

    def _get_segment_secs(self):
        idx = self.segment_combo.currentIndex()
        return self._segment_options[idx][1]

    @staticmethod
    def _parse_crop_secs(text):
        """Parse a HH:MM:SS, MM:SS, or plain seconds string into total
        seconds. Returns 0 if the text is empty or invalid."""
        text = (text or "").strip()
        if not text:
            return 0
        parts = text.split(":")
        try:
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
            return int(float(text))
        except (ValueError, IndexError):
            return 0

    @staticmethod
    def _fmt_crop_time(secs):
        """Format seconds as HH:MM:SS for log output."""
        s = int(secs)
        return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"

    def _is_audio_only(self):
        """Detect if the current stream is audio-only based on its qualities."""
        if not self.stream_info:
            return False
        if not self.stream_info.qualities:
            return False
        return all(
            (q.resolution or "").lower() == "audio" or "audio" in (q.name or "").lower()
            for q in self.stream_info.qualities
        )

    def _content_label(self, idx, total_segments, seg_secs, total_secs):
        """Generate a content-aware segment label."""
        is_audio = self._is_audio_only()
        kind = "Audio" if is_audio else "Video"
        if total_secs <= 0:
            return "Live Capture" if not is_audio else "Live Audio"

        if total_segments == 1:
            # Single segment — use the content type
            if total_secs < 60:
                return f"{kind} ({int(total_secs)}s)"
            elif total_secs < 3600:
                return f"{kind} ({int(total_secs // 60)}m)"
            else:
                return f"{kind} ({_fmt_duration(total_secs)})"

        # Multi-segment naming based on segment length
        if seg_secs >= 3600:
            return f"Hour {idx + 1}"
        elif seg_secs >= 60:
            mins = seg_secs // 60
            return f"Part {idx + 1} ({mins}m)"
        else:
            return f"Part {idx + 1}"

    def _build_segments(self, total_secs):
        if total_secs <= 0:
            segments = [(0, 0)]
            seg_secs = 0
        else:
            seg_secs = self._get_segment_secs()

            # Auto-collapse: if content is shorter than segment length, use one segment
            if seg_secs == 0 or total_secs <= seg_secs:
                segments = [(0, total_secs)]
            else:
                segments = []
                pos = 0
                while pos < total_secs:
                    end = min(pos + seg_secs, total_secs)
                    segments.append((pos, end))
                    pos = end

        self.table.setRowCount(len(segments))
        if hasattr(self, "segments_section"):
            self.segments_section.setVisible(bool(segments))
        self._segment_checks = []
        self._segment_progress = []

        for i, (start, end) in enumerate(segments):
            duration = end - start
            cb = QCheckBox()
            cb.setChecked(True)
            cb.setAccessibleName(f"Include segment {i + 1}")
            cb.stateChanged.connect(lambda _state, self=self: self._refresh_download_summary())
            cb_w = QWidget()
            cb_l = QHBoxLayout(cb_w)
            cb_l.addWidget(cb)
            cb_l.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cb_l.setContentsMargins(0, 0, 0, 0)
            self.table.setCellWidget(i, 0, cb_w)
            self._segment_checks.append(cb)

            label = self._content_label(i, len(segments), seg_secs, total_secs)
            item = QTableWidgetItem(label)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(i, 1, item)

            if total_secs <= 0:
                range_text = "Starts now - runs until stopped"
            else:
                s_str = f"{int(start//3600):02d}:{int((start%3600)//60):02d}:{int(start%60):02d}"
                e_str = f"{int(end//3600):02d}:{int((end%3600)//60):02d}:{int(end%60):02d}"
                range_text = f"{s_str} - {e_str}  ({int(duration//60)}m {int(duration%60)}s)"
            t_item = QTableWidgetItem(range_text)
            t_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(i, 2, t_item)

            pbar = QProgressBar()
            pbar.setAccessibleName(f"Segment {i + 1} download progress")
            if total_secs <= 0:
                pbar.setMaximum(0)
            else:
                pbar.setValue(0)
            self.table.setCellWidget(i, 3, pbar)
            self._segment_progress.append(pbar)

            # Estimated size: bandwidth (bits/sec) × duration / 8 = bytes
            est = self._estimate_size_bytes(duration)
            sz_text = f"~{_fmt_size(est)}" if est > 0 else "\u2014"
            sz = QTableWidgetItem(sz_text)
            sz.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            sz.setForeground(QColor(CAT["muted"]))
            self.table.setItem(i, 4, sz)
        self._refresh_download_summary()

    def _estimate_size_bytes(self, duration_secs):
        """Return estimated file size in bytes using the selected quality's bandwidth."""
        if duration_secs <= 0:
            return 0
        q = self.quality_combo.currentData() if hasattr(self, "quality_combo") else None
        if not q or not getattr(q, "bandwidth", 0):
            return 0
        # bandwidth is bits/sec; convert to bytes
        return int(q.bandwidth * duration_secs / 8)

    def _on_select_all(self, state):
        checked = state == Qt.CheckState.Checked.value
        for cb in self._segment_checks:
            cb.setChecked(checked)
        self._refresh_download_summary()

    def _on_segment_length_changed(self, idx):
        if self.stream_info and self.stream_info.total_secs > 0:
            self._build_segments(self.stream_info.total_secs)
        else:
            self._refresh_download_summary()

    def _on_quality_changed(self, idx):
        """Rebuild size estimates in the segment table when quality changes."""
        _populate_track_table(self)
        if not self.stream_info or not hasattr(self, "_segment_progress"):
            return
        total_secs = self.stream_info.total_secs
        if total_secs <= 0:
            return
        seg_secs = self._get_segment_secs()
        if seg_secs == 0 or total_secs <= seg_secs:
            durations = [total_secs]
        else:
            durations = []
            pos = 0
            while pos < total_secs:
                end = min(pos + seg_secs, total_secs)
                durations.append(end - pos)
                pos = end
        for i, d in enumerate(durations):
            if i >= self.table.rowCount():
                break
            est = self._estimate_size_bytes(d)
            sz_text = f"~{_fmt_size(est)}" if est > 0 else "\u2014"
            sz = QTableWidgetItem(sz_text)
            sz.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            sz.setForeground(QColor(CAT["muted"]))
            self.table.setItem(i, 4, sz)

    def _on_browse(self):
        d = QFileDialog.getExistingDirectory(self, "Select Output Folder", self.output_input.text())
        if d:
            self.output_input.setText(d)

    def _on_copy_download_command(self):
        command = str(getattr(self, "_export_command_text", "") or "")
        if not command:
            self._set_status(
                "Start a prepared download before copying its command.", "info"
            )
            return
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(command)
        self._set_status(
            "Standalone command copied. It may include cookie paths or headers.",
            "success",
        )


    # ── Download core ───────────────────────────────────────────

    def _on_download(self):
        if not self.stream_info:
            return False
        src_url = self.url_input.text().strip() if hasattr(self, "url_input") else ""
        if src_url and not self.stream_info.is_live:
            prev = _db.find_history_by_url(src_url)
            if prev:
                from PyQt6.QtWidgets import QMessageBox
                ans = QMessageBox.question(
                    self, "Already Downloaded",
                    f"This URL was downloaded on {prev.get('date', '?')}\n"
                    f"to: {prev.get('path', '?')[:80]}\n\n"
                    "Download again?",
                )
                if ans != QMessageBox.StandardButton.Yes:
                    return False
        # Disk-space preflight — catches "no more room on device" before
        # ffmpeg runs for three hours and exits with a muxing error. Only
        # warns when we have a meaningful estimate; lives / unknown-duration
        # streams skip the check.
        if not self._preflight_disk_space():
            return False
        total_secs = self.stream_info.total_secs
        is_live_capture = bool(self.stream_info.is_live or total_secs <= 0)

        q_data = self.quality_combo.currentData()
        selected_tracks = get_selected_media_tracks(self)
        audio_url = ""
        ytdlp_source = ""
        ytdlp_format = ""
        if q_data:
            playlist_url = q_data.url
            fmt_type = q_data.format_type
            audio_url = q_data.audio_url
            ytdlp_source = q_data.ytdlp_source
            ytdlp_format = q_data.ytdlp_format
            if q_data.tracks and not any(
                track.kind in {"video", "audio"} for track in selected_tracks
            ):
                self._log("[ERROR] No playable media tracks selected")
                self._set_status(
                    "Select at least one video or audio track.", "warning"
                )
                return False
        elif self.stream_info.url:
            playlist_url = self.stream_info.url
            fmt_type = "hls"
        else:
            self._log("[ERROR] No quality selected")
            self._set_status("Pick a quality before starting the download.", "warning")
            return False

        # Per-download overrides (F18)
        _dl_overrides = get_adv_overrides(self)
        ytdlp_override_keys = {
            "format_spec", "format_sort_preset", "container",
            "audio_format", "audio_quality", "subtitle_mode",
            "sponsorblock_mode",
        }
        ytdlp_override_keys.update(
            key for key in _dl_overrides if key.startswith("ytdlp_")
        )
        active_ytdlp_overrides = ytdlp_override_keys.intersection(_dl_overrides)
        if active_ytdlp_overrides and fmt_type != "ytdlp_direct":
            self._log(
                "[OUTPUT] Format/container/audio controls require a yt-dlp direct quality."
            )
            self._set_status(
                "These output controls apply only to yt-dlp direct sources; "
                "choose a yt-dlp quality or reset them.",
                "warning",
            )
            return False
        if _dl_overrides.get("audio_format") and _dl_overrides.get("container"):
            self._set_status(
                "Choose either a video container or audio extraction, not both.",
                "warning",
            )
            return False
        try:
            from ...download_options import (
                validate_hls_key_override,
                resolve_ytdlp_arg_template,
                resolve_ytdlp_transfer_options,
                validate_download_options, validate_sponsorblock_options,
                validate_subtitle_options,
            )
            ytdlp_options = validate_download_options(
                format_spec=_dl_overrides.get("format_spec", ""),
                format_sort_preset=_dl_overrides.get("format_sort_preset", ""),
                container=_dl_overrides.get("container", ""),
                audio_format=_dl_overrides.get("audio_format", ""),
                audio_quality=_dl_overrides.get("audio_quality", ""),
            )
            subtitle_mode = _dl_overrides.get("subtitle_mode", "")
            if subtitle_mode == "disabled":
                subtitle_options = validate_subtitle_options(enabled=False)
            elif subtitle_mode == "custom":
                subtitle_options = validate_subtitle_options(
                    enabled=True,
                    languages=_dl_overrides.get("subtitle_languages", ""),
                    automatic=_dl_overrides.get("subtitle_auto", True),
                    convert=_dl_overrides.get("subtitle_convert", ""),
                    embed=_dl_overrides.get("subtitle_embed", True),
                )
            else:
                subtitle_options = validate_subtitle_options(
                    enabled=YtDlpExtractor.download_subs,
                    languages=YtDlpExtractor.subtitle_languages,
                    automatic=YtDlpExtractor.subtitle_auto,
                    convert=YtDlpExtractor.subtitle_convert,
                    embed=YtDlpExtractor.subtitle_embed,
                )
            sponsorblock_mode = _dl_overrides.get("sponsorblock_mode", "")
            if sponsorblock_mode == "disabled":
                sponsorblock_options = validate_sponsorblock_options(
                    enabled=False
                )
            elif sponsorblock_mode == "custom":
                sponsorblock_options = validate_sponsorblock_options(
                    enabled=True,
                    mark=_dl_overrides.get("sponsorblock_mark", ""),
                    remove=_dl_overrides.get("sponsorblock_remove", ""),
                    api_url=_dl_overrides.get("sponsorblock_api", ""),
                )
            else:
                sponsorblock_options = validate_sponsorblock_options(
                    enabled=YtDlpExtractor.sponsorblock,
                    mark=YtDlpExtractor.sponsorblock_mark,
                    remove=YtDlpExtractor.sponsorblock_remove,
                    api_url=YtDlpExtractor.sponsorblock_api,
                )
            transfer_options = resolve_ytdlp_transfer_options(
                YtDlpExtractor, overrides=_dl_overrides,
            )
            ytdlp_template_name = _dl_overrides.get(
                "ytdlp_template_name", ""
            )
            ytdlp_template_args = resolve_ytdlp_arg_template(
                self._config.get("ytdlp_arg_templates", {}),
                ytdlp_template_name,
            )
            hls_key_options = validate_hls_key_override(
                _dl_overrides.get("hls_key_override", ""),
                _dl_overrides.get("hls_key_iv", ""),
            )
        except ValueError as error:
            self._log(f"[OUTPUT] Invalid per-download settings: {error}")
            self._set_status(str(error), "warning")
            return False

        if hls_key_options["value"]:
            if fmt_type not in {"hls", "ytdlp_direct"}:
                self._set_status(
                    "The clear-key override applies only to non-DRM HLS sources.",
                    "warning",
                )
                return False
            selected_urls = {
                track.url for track in selected_tracks if track.url
            }
            from ...models import default_media_tracks
            default_ids = {
                track.id for track in default_media_tracks(q_data)
            } if q_data else set()
            selected_ids = {track.id for track in selected_tracks}
            if (len(selected_urls) > 1
                    or (default_ids and selected_ids != default_ids)):
                self._set_status(
                    "Clear-key recovery supports the default tracks from one HLS "
                    "media playlist; load that playlist directly for custom tracks.",
                    "warning",
                )
                return False

        if (subtitle_mode == "custom" and ytdlp_options["audio_format"]
                and subtitle_options["embed"]):
            self._set_status(
                "Audio extraction cannot embed subtitles; choose Sidecar.",
                "warning",
            )
            return False

        if ytdlp_options["format_spec"]:
            ytdlp_format = ytdlp_options["format_spec"]
        elif ytdlp_options["audio_format"]:
            ytdlp_format = "bestaudio/best"

        # Render filename + folder from templates (templates can produce
        # nested paths like "{channel}/{date} - {title}")
        ctx = _build_template_context(self.stream_info)
        _folder_tpl = _dl_overrides.get("folder_template") or self._folder_template
        _file_tpl = _dl_overrides.get("file_template") or self._file_template
        folder_parts = _render_template(_folder_tpl, ctx)
        file_parts = _render_template(_file_tpl, ctx)
        title_safe = file_parts[-1] if file_parts else (
            _safe_filename(self.stream_info.title)
            or f"{self.stream_info.platform}_download"
        )

        # Time-range crop (F21) — parse optional start/end bounds
        crop_start = self._parse_crop_secs(
            self.crop_start_input.text() if hasattr(self, "crop_start_input") else ""
        )
        crop_end = self._parse_crop_secs(
            self.crop_end_input.text() if hasattr(self, "crop_end_input") else ""
        )
        if crop_end and crop_start and crop_end <= crop_start:
            self._set_status("Time range end must be after start.", "warning")
            return False

        seg_secs = self._get_segment_secs()
        single_segment = (is_live_capture or hls_key_options["value"]
                          or fmt_type in ("mp4", "ytdlp_direct")
                          or seg_secs == 0 or total_secs <= seg_secs)
        segments = []
        for i, cb in enumerate(self._segment_checks):
            if cb.isChecked():
                if single_segment:
                    seg_start = crop_start or 0
                    seg_dur = (crop_end or (0 if is_live_capture else int(total_secs))) - seg_start
                    segments.append((0, title_safe, seg_start, max(0, seg_dur)))
                    break
                else:
                    start = i * seg_secs
                    end = min((i + 1) * seg_secs, total_secs)
                    # Skip segments entirely outside the crop window
                    if crop_end and start >= crop_end:
                        continue
                    if crop_start and end <= crop_start:
                        continue
                    # Clamp segment bounds to the crop window
                    if crop_start and start < crop_start:
                        start = crop_start
                    if crop_end and end > crop_end:
                        end = crop_end
                    label = f"{title_safe}_part{i + 1:02d}"
                    segments.append((i, label, start, int(end - start)))

        if not segments:
            self._log("No segments selected.")
            self._set_status("Select at least one segment before downloading.", "warning")
            return False

        if crop_start or crop_end:
            self._log(f"[CROP] Time range: {self._fmt_crop_time(crop_start)} → {self._fmt_crop_time(crop_end or total_secs)}")

        # For non-channel content, user's output box is the base; folder template
        # adds a subfolder. For channel content the template already has
        # {channel} in it, so joining still works.
        base_out = self.output_input.text().strip()
        if folder_parts:
            out_dir = os.path.join(base_out, *folder_parts)
        else:
            out_dir = base_out
        os.makedirs(out_dir, exist_ok=True)

        self._log(f"\n{'=' * 50}")
        self._log(f"Downloading {len(segments)} segments to {out_dir}")
        self._log(f"Quality: {self.quality_combo.currentText()}")
        self._log(f"{'=' * 50}")

        self._total_segments = len(segments)
        self._completed_segments = 0
        self._download_had_errors = False
        self._init_speed_tracking()
        self.download_btn.setEnabled(False)
        self.fetch_btn.setEnabled(False)
        self.stop_btn.setVisible(True)
        self.open_folder_btn.setVisible(False)
        self.overall_progress.setVisible(True)
        self.overall_progress.setValue(0)
        self.overall_progress.setMaximum(len(segments))
        if is_live_capture:
            self._set_status(
                f"Live capture started. Recording to {_path_label(out_dir)} until you stop it.",
                "working",
            )
        else:
            self._set_status(
                f"Downloading 0 of {len(segments)} segment(s) to {_path_label(out_dir)}.",
                "working",
            )

        self._set_download_context(
            out_dir=out_dir,
            quality_name=self.quality_combo.currentText(),
            history_url=self._resolve_history_url(),
            info=self.stream_info,
        )
        self.download_worker = DownloadWorker(playlist_url or "", segments, out_dir, format_type=fmt_type)
        self.download_worker.audio_url = audio_url
        self.download_worker.selected_tracks = selected_tracks
        self.download_worker.ytdlp_source = ytdlp_source
        self.download_worker.ytdlp_format = ytdlp_format
        self.download_worker.ytdlp_format_sort = ytdlp_options["format_sort"]
        self.download_worker.ytdlp_container = ytdlp_options["container"]
        self.download_worker.ytdlp_audio_format = ytdlp_options["audio_format"]
        self.download_worker.ytdlp_audio_quality = ytdlp_options["audio_quality"]
        self.download_worker.cookies_browser = YtDlpExtractor.cookies_browser
        self.download_worker.rate_limit = _dl_overrides.get("rate_limit") or YtDlpExtractor.rate_limit
        self.download_worker.proxy = YtDlpExtractor.proxy
        self.download_worker.download_subs = subtitle_options["enabled"]
        self.download_worker.capture_youtube_chat = YtDlpExtractor.capture_youtube_chat
        self.download_worker.subtitle_languages = subtitle_options["languages"]
        self.download_worker.subtitle_auto = subtitle_options["automatic"]
        self.download_worker.subtitle_convert = subtitle_options["convert"]
        self.download_worker.subtitle_embed = subtitle_options["embed"]
        self.download_worker.sponsorblock = sponsorblock_options["enabled"]
        self.download_worker.sponsorblock_mark = sponsorblock_options["mark"]
        self.download_worker.sponsorblock_remove = sponsorblock_options["remove"]
        self.download_worker.sponsorblock_api = sponsorblock_options["api_url"]
        from ...download_options import (
            apply_external_downloader_options, apply_ytdlp_transfer_options,
        )
        apply_ytdlp_transfer_options(
            self.download_worker,
            transfer_options,
        )
        apply_external_downloader_options(self.download_worker, YtDlpExtractor)
        self.download_worker.ytdlp_template_name = ytdlp_template_name
        self.download_worker.ytdlp_template_args = ytdlp_template_args
        self.download_worker.hls_key_override = hls_key_options["value"]
        self.download_worker.hls_key_iv = hls_key_options["iv"]
        self.download_worker.parallel_connections = _dl_overrides.get("parallel_connections") or self._parallel_connections
        # Pass time-range crop to yt-dlp via --download-sections (F21)
        if ((fmt_type == "ytdlp_direct" or hls_key_options["value"])
                and (crop_start or crop_end)):
            cs = self._fmt_crop_time(crop_start) if crop_start else "0:00:00"
            ce = self._fmt_crop_time(crop_end) if crop_end else ""
            self.download_worker.download_sections = f"*{cs}-{ce}" if ce else f"*{cs}-"
        if hls_key_options["value"]:
            self._log(
                "[HLS] Authorized clear-key override enabled for this job; "
                "the value will not be persisted."
            )
        if audio_url:
            self._log("Audio merge: enabled (video-only format detected)")
        if fmt_type == "ytdlp_direct":
            self._log("Download mode: yt-dlp direct (handles URL refresh + format merge)")
            if ytdlp_options["audio_format"]:
                detail = ytdlp_options["audio_format"]
                if ytdlp_options["audio_quality"]:
                    detail += f" @ {ytdlp_options['audio_quality']}"
                self._log(f"[OUTPUT] Audio extraction: {detail}")
            else:
                self._log(
                    f"[OUTPUT] Video container: {ytdlp_options['container']}"
                )
            if ytdlp_options["format_sort"]:
                self._log(f"[OUTPUT] Format sort: {ytdlp_options['format_sort']}")
            if subtitle_options["enabled"]:
                delivery = "embedded" if subtitle_options["embed"] else "sidecar"
                conversion = subtitle_options["convert"] or "source format"
                auto = "+ auto" if subtitle_options["automatic"] else "manual only"
                self._log(
                    f"[SUBS] {subtitle_options['languages']} | {auto} | "
                    f"{conversion} | {delivery}"
                )
            if sponsorblock_options["enabled"]:
                self._log(
                    "[SPONSORBLOCK] Mark: "
                    f"{sponsorblock_options['mark'] or 'none'} | Remove: "
                    f"{sponsorblock_options['remove'] or 'none'}"
                )
            if ytdlp_template_name:
                self._log(
                    f"[ARGS] Named yt-dlp template: {ytdlp_template_name}"
                )
        try:
            self._export_command_text = self.download_worker.export_command()
            self.copy_command_btn.setEnabled(True)
        except (TypeError, ValueError) as error:
            self._export_command_text = ""
            self.copy_command_btn.setEnabled(False)
            self._log(f"[EXPORT] Could not build standalone command: {error}")
        self.download_worker.progress.connect(self._on_dl_progress)
        self.download_worker.segment_done.connect(self._on_segment_done)
        self.download_worker.error.connect(self._on_dl_error)
        self.download_worker.log.connect(self._log)
        self.download_worker.all_done.connect(self._on_all_done)
        self.download_worker.finished.connect(self._on_download_worker_finished)
        self._attach_resume_to_worker(self.download_worker)
        # Store overrides for postprocess snapshot merge (F18)
        self._dl_overrides = _dl_overrides
        if _dl_overrides:
            self._log(f"[OVERRIDE] Per-download overrides active: {', '.join(_dl_overrides.keys())}")
        _reset_adv_overrides(self)
        self.download_worker.start()
        return True

    def _on_download_worker_finished(self):
        if not getattr(self, "_download_had_errors", False):
            return
        self.download_btn.setEnabled(True)
        self.fetch_btn.setEnabled(True)
        self.stop_btn.setVisible(False)
        self.open_folder_btn.setVisible(self._output_contains_media(self._active_output_dir))
        if self._queue_active_item is not None:
            note = f"{getattr(self, '_completed_segments', 0)}/{getattr(self, '_total_segments', 0)} segments completed"
            self._release_queue_item("failed", note)
        self._set_status(
            "Download stopped after failed segment(s). Resume sidecar was kept for retry.",
            "warning",
        )
        self._persist_config()
        self._update_tray_badge()
        self._reset_speed_dashboard()
        self._start_next_background_job()

    def _on_dl_progress(self, idx, pct, status):
        if idx < len(self._segment_progress):
            if self.stream_info and (self.stream_info.is_live or self.stream_info.total_secs <= 0):
                self._segment_progress[idx].setMaximum(0)
            else:
                self._segment_progress[idx].setMaximum(100)
                self._segment_progress[idx].setValue(pct)
        if hasattr(self, "_total_segments") and self._total_segments:
            self._set_status(
                f"Downloading {self._completed_segments}/{self._total_segments}. Segment {idx + 1}: {status}",
                "working",
            )
        # Parse speed from the status text (F16 speed dashboard)
        self._update_speed_from_status(status)

    def _on_segment_done(self, idx, size_str):
        if idx < len(self._segment_progress):
            self._segment_progress[idx].setMaximum(100)
            self._segment_progress[idx].setValue(100)
            self._segment_progress[idx].setStyleSheet(
                f"QProgressBar::chunk {{ background-color: {CAT['green']}; border-radius: 6px; }}"
            )
        size_item = QTableWidgetItem(size_str)
        size_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.setItem(idx, 4, size_item)
        self._completed_segments += 1
        self.overall_progress.setValue(self._completed_segments)
        self._set_status(
            f"Downloaded {self._completed_segments} of {self._total_segments} segment(s).",
            "working",
        )

    def _on_dl_error(self, idx, err):
        self._download_had_errors = True
        self._record_failed_job(
            stage="download",
            error=err,
            item=self._queue_active_item,
            info=self._active_stream_info or self.stream_info,
            out_dir=self._active_output_dir,
        )
        if idx < len(self._segment_progress):
            self._segment_progress[idx].setStyleSheet(
                f"QProgressBar::chunk {{ background-color: {CAT['red']}; border-radius: 6px; }}"
            )
        fail_item = QTableWidgetItem("FAILED")
        fail_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.setItem(idx, 4, fail_item)
        self._set_status(f"Segment {idx + 1} failed: {err}", "error")

    def _on_all_done(self):
        self.download_btn.setEnabled(True)
        self.fetch_btn.setEnabled(True)
        self.stop_btn.setVisible(False)
        self.open_folder_btn.setVisible(True)
        if hasattr(self, "trim_btn"):
            self.trim_btn.setVisible(True)
        active_info_n = self._active_stream_info or self.stream_info
        title_n = (active_info_n.title if active_info_n and active_info_n.title else "Download")[:80]
        self._notify_center(
            f"Download complete: {title_n}",
            "success" if not self._download_had_errors else "warning",
        )
        active_info = self._active_stream_info or self.stream_info
        out_dir = self._active_output_dir or self.output_input.text().strip()
        q_name = self._active_quality_name or (
            self.quality_combo.currentText() if self.quality_combo.count() else ""
        )
        title = active_info.title if active_info and active_info.title else "Download"

        self._log(f"\n{'=' * 50}")
        if self._download_had_errors:
            self._log("Download finished with one or more failed segments.")
            self._log(f"{'=' * 50}")
            self._record_failed_job(
                stage="download",
                error=f"{self._completed_segments}/{self._total_segments} segments completed",
                item=self._queue_active_item,
                info=active_info,
                out_dir=out_dir,
            )
            self._set_status(
                "Download finished with one or more failed segments. Review the log before retrying.",
                "warning",
            )
            if self._queue_active_item is not None:
                note = f"{self._completed_segments}/{self._total_segments} segments completed"
                self._release_queue_item("failed", note)
        else:
            self._log("All downloads complete!")
            self._log(f"{'=' * 50}")
            if active_info and (active_info.is_live or active_info.total_secs <= 0):
                self._set_status("Live capture finished and was saved to the selected folder.", "success")
                self._notify("StreamKeep — Capture finished", title[:80])
                self._send_webhook("capture finished", title,
                                   f"Segments: {self._completed_segments}")
            else:
                self._set_status(
                    f"Download complete. Saved {self._completed_segments} segment(s) to the selected folder.",
                    "success",
                )
                self._notify("StreamKeep — Download complete", title[:80])
                self._send_webhook("download complete", title,
                                   f"Segments: {self._completed_segments}")
                self._fire_hook(
                    "download_complete", title=title,
                    path=out_dir,
                    platform=active_info.platform if active_info else "")
                _db.mark_failed_jobs_resolved_for_url(self._active_history_url)
            self._save_metadata(
                out_dir,
                q_name,
                history_url=self._active_history_url,
                info=active_info,
            )
            self._media_server_import(out_dir, active_info)
            if self._queue_active_item is not None:
                self._release_queue_item("done")
        self._persist_config()
        self._run_lifecycle_cleanup()
        self._update_tray_badge()
        self._reset_speed_dashboard()
        self._start_next_background_job()

    def _on_stop(self):
        worker = self.download_worker
        resume_background_jobs = bool(
            self._queue_active_item is not None
            or self._autorecord_workers
            or self._autorecord_resolvers
            or self._pending_auto_records
        )
        live_capture = bool(
            worker and any(len(seg) >= 4 and seg[3] <= 0 for seg in getattr(worker, "segments", []))
        )
        # Halt any in-progress batch by marking it done
        if hasattr(self, '_batch_vods') and hasattr(self, '_batch_total'):
            self._batch_active = False
            self._batch_idx = self._batch_total
            self._cancel_batch_fetch_worker()
        if self.download_worker is not None:
            try:
                self.download_worker.cancel()
                if not self.download_worker.wait(5000):
                    self.download_worker.terminate()
                    self.download_worker.wait(1000)
            except Exception:
                pass
            self.download_worker = None
        # Also stop any parallel auto-records. The stop button is a global
        # "halt everything the user is actively watching" — parallel lives
        # included. (Use the Monitor tab's per-row Stop+Remove for selective
        # stops.)
        for ch_id in list(self._autorecord_workers.keys()):
            w = self._autorecord_workers.get(ch_id)
            if w is not None and w.isRunning():
                try:
                    w.cancel()
                    if not w.wait(3000):
                        w.terminate()
                        w.wait(500)
                except Exception:
                    pass
        self._autorecord_workers.clear()
        self._autorecord_contexts.clear()
        for ch_id in list(self._autorecord_resolvers.keys()):
            w = self._autorecord_resolvers.get(ch_id)
            if w is not None and w.isRunning():
                try:
                    w.requestInterruption()
                    w.wait(1500)
                except Exception:
                    pass
        self._autorecord_resolvers.clear()
        # Stop any paired live-chat captures.
        for ch_id in list(self._chat_workers.keys()):
            w = self._chat_workers.get(ch_id)
            if w is not None and w.isRunning():
                try:
                    w.cancel()
                    w.wait(2000)
                except Exception:
                    pass
        self._chat_workers.clear()
        # Clear any green/red chunk overrides left on segment bars so the
        # next download starts from a neutral style instead of inheriting
        # the previous run's success/fail colors.
        for pbar in getattr(self, "_segment_progress", []):
            try:
                pbar.setStyleSheet("")
                pbar.setValue(0)
            except Exception:
                pass
        self.download_btn.setEnabled(True)
        self.fetch_btn.setEnabled(True)
        self.stop_btn.setVisible(False)
        self.overall_progress.setVisible(False)
        if hasattr(self, 'vod_dl_all_btn'):
            self.vod_dl_all_btn.setEnabled(True)
            self.vod_load_btn.setEnabled(True)
        for entry in self.monitor.entries:
            entry.is_recording = False
        self._active_auto_record_channel = ""
        self._refresh_monitor_summary()
        self._log("[CANCELLED] Download stopped by user.")
        if self._queue_active_item is not None:
            self._release_queue_item("cancelled", "Stopped by user")
        if live_capture:
            has_media = self._output_contains_media(self._active_output_dir)
            self.open_folder_btn.setVisible(has_media)
            if has_media and self._active_output_dir and self._active_stream_info:
                self._save_metadata(
                    self._active_output_dir,
                    self._active_quality_name,
                    history_url=self._active_history_url,
                    info=self._active_stream_info,
                )
                self._set_status("Recording stopped. Any captured portion was kept on disk.", "warning")
            else:
                self._set_status("Recording stopped before any media was saved.", "warning")
        else:
            self._set_status("Download cancelled. You can adjust the selection and try again.", "warning")
        if resume_background_jobs:
            self._start_next_background_job()

    def _on_open_folder(self):
        out_dir = self._active_output_dir or self.output_input.text().strip()
        if os.path.isdir(out_dir):
            QDesktopServices.openUrl(QUrl.fromLocalFile(out_dir))


    # ── Speed / ETA tracking ────────────────────────────────────

    def _init_speed_tracking(self):
        """Reset speed tracking state at the start of a download."""
        self._speed_samples = deque(maxlen=60)  # (timestamp, speed_bytes_per_sec)
        self._dl_start_time = time.monotonic()
        if hasattr(self, "download_speed_value"):
            self.download_speed_value.setText("—")
            self.download_speed_sub.setText("Waiting for data")
        if hasattr(self, "download_eta_value"):
            self.download_eta_value.setText("—")
            self.download_eta_sub.setText("Estimating...")

    def _update_speed_from_status(self, status):
        """Parse speed info from the progress status string and update the
        speed/ETA dashboard cards."""
        if not hasattr(self, "download_speed_value"):
            return
        # Try to extract a speed like "12.4MB/s" or "3.2MiB/s" from the status
        m = re.search(r'([\d.]+)\s*(B|KB|KiB|MB|MiB|GB|GiB)/s', status, re.IGNORECASE)
        if not m:
            return
        val = float(m.group(1))
        unit = m.group(2).upper().replace("I", "")
        multipliers = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}
        bps = val * multipliers.get(unit, 1)
        now = time.monotonic()
        self._speed_samples.append((now, bps))
        # Compute 5-second smoothed average
        cutoff = now - 5.0
        recent = [(t, s) for t, s in self._speed_samples if t >= cutoff]
        if recent:
            avg_speed = sum(s for _, s in recent) / len(recent)
        else:
            avg_speed = bps
        # Display speed
        self.download_speed_value.setText(_fmt_size(int(avg_speed)) + "/s")
        self.download_speed_sub.setText(f"5-sec avg ({len(recent)} samples)")
        # Calculate ETA from remaining segments
        total = getattr(self, "_total_segments", 0)
        done = getattr(self, "_completed_segments", 0)
        if total > 0 and done < total and avg_speed > 0:
            # Estimate bytes remaining from elapsed speed and segment ratio
            elapsed = now - getattr(self, "_dl_start_time", now)
            if elapsed > 0 and done > 0:
                est_total_time = elapsed * total / done
                remaining = est_total_time - elapsed
                if remaining > 0:
                    self.download_eta_value.setText(_fmt_duration(remaining))
                    self.download_eta_sub.setText(
                        f"{done}/{total} segments done"
                    )
                    return
            self.download_eta_value.setText("Estimating...")
        elif total > 0 and done >= total:
            self.download_eta_value.setText("Done")
            self.download_eta_sub.setText("Finalizing...")

    def _reset_speed_dashboard(self):
        """Clear speed/ETA cards after download completes."""
        if hasattr(self, "download_speed_value"):
            self.download_speed_value.setText("—")
            self.download_speed_sub.setText("Starts during download")
        if hasattr(self, "download_eta_value"):
            self.download_eta_value.setText("—")
            self.download_eta_sub.setText("Estimated time remaining")


    # ── Playlist / page scrape ──────────────────────────────────

    def _on_expand_playlist(self):
        """Probe the URL for playlist/channel entries and queue them all."""
        url = self.url_input.text().strip()
        if not url:
            self._set_status("Paste a URL first.", "warning")
            return
        overrides = get_adv_overrides(self)
        archive_path = ""
        if overrides.get("playlist_archive_sync"):
            from ...paths import source_archive_path
            archive_path = source_archive_path(url)
        try:
            from ...download_options import validate_playlist_options
            options = validate_playlist_options(
                items=overrides.get("playlist_items", ""),
                date_after=overrides.get("playlist_date_after", ""),
                date_before=overrides.get("playlist_date_before", ""),
                match_filter=overrides.get("playlist_match_filter", ""),
                max_downloads=overrides.get("playlist_max_downloads", 0),
                archive_path=archive_path,
                break_on_existing=bool(archive_path),
            )
        except ValueError as error:
            self._set_status(str(error), "warning")
            return
        self.expand_btn.setEnabled(False)
        self._set_status("Probing for playlist/channel entries...", "working")
        self._log(f"[PLAYLIST] Probing: {url}")
        # Run in a throwaway thread to avoid blocking the UI
        worker = _PlaylistExpandWorker(
            url,
            playlist_items=options["items"],
            date_after=options["date_after"],
            date_before=options["date_before"],
            match_filter=options["match_filter"],
            max_downloads=options["max_downloads"],
            archive_path=options["archive_path"],
            break_on_existing=options["break_on_existing"],
        )
        worker.finished.connect(
            lambda entries, u=url, o=options: self._on_expand_done(u, entries, o)
        )
        worker.error.connect(self._on_expand_error)
        worker.log.connect(self._log)
        self._expand_worker = worker
        worker.start()

    def _on_expand_done(self, source_url, entries, options=None):
        self.expand_btn.setEnabled(True)
        if not entries:
            if options and options.get("archive_path"):
                self._log("[PLAYLIST] Incremental archive is already current")
                self._set_status(
                    "Archive sync is current; no new playlist entries were queued.",
                    "success",
                )
                return
            self._set_status(
                "No playlist entries found. This URL may be a single video — use Fetch instead.",
                "warning",
            )
            return
        added = 0
        options = options or {}
        for e in entries:
            if self._queue_add(
                e.get("url", ""), title=e.get("title", ""),
                platform="yt-dlp",
                download_archive=options.get("archive_path", ""),
                break_on_existing=options.get("break_on_existing", False),
            ):
                added += 1
        self._log(f"[PLAYLIST] Queued {added} new of {len(entries)} total entries")
        self._set_status(
            f"Playlist expanded. Queued {added} new entries "
            f"({len(entries) - added} already in the queue).",
            "success",
        )
        # Kick off the queue if nothing's downloading
        worker = getattr(self, "download_worker", None)
        if worker is None or not worker.isRunning():
            self._advance_queue()

    def _on_expand_error(self, err):
        self.expand_btn.setEnabled(True)
        self._log(f"[PLAYLIST] {err}")
        self._set_status(f"Playlist probe failed: {err}", "error")

    def _on_scan_page(self):
        """Scrape a webpage for video/media links and queue them."""
        url = self.url_input.text().strip()
        if not url:
            self._set_status("Paste a webpage URL first.", "warning")
            return
        if not url.startswith("http"):
            self._set_status("Scan Page expects a full http(s) URL.", "warning")
            return
        self.scan_btn.setEnabled(False)
        if hasattr(self, "scan_action"):
            self.scan_action.setEnabled(False)
        allow_lan = self.scan_lan_check.isChecked()
        self.scan_lan_check.setChecked(False)
        self._set_status("Scanning page for media links...", "working")
        self._log(
            f"[SCRAPE] Scanning {url} "
            f"(LAN override {'enabled for this scan' if allow_lan else 'off'})"
        )
        worker = _PageScrapeWorker(
            url,
            allow_private_network=allow_lan,
        )
        worker.finished.connect(self._on_scan_done)
        worker.error.connect(self._on_scan_error)
        worker.log.connect(self._log)
        self._scan_worker = worker
        worker.start()

    def _on_scan_done(self, links):
        self.scan_btn.setEnabled(True)
        if hasattr(self, "scan_action"):
            self.scan_action.setEnabled(True)
        if not links:
            self._set_status(
                "No media links found. Try Fetch or Expand Playlist instead.",
                "warning",
            )
            return
        added = 0
        for url, hint in links:
            if self._queue_add(url, title=url[:80], platform=hint):
                added += 1
        self._log(f"[SCRAPE] Queued {added} new link(s) of {len(links)} found")
        self._set_status(
            f"Found {len(links)} link(s). Queued {added} new ({len(links) - added} already in queue).",
            "success",
        )
        worker = getattr(self, "download_worker", None)
        if worker is None or not worker.isRunning():
            self._advance_queue()

    def _on_scan_error(self, err):
        self.scan_btn.setEnabled(True)
        if hasattr(self, "scan_action"):
            self.scan_action.setEnabled(True)
        self._log(f"[SCRAPE] {err}")
        self._set_status(f"Scan failed: {err}", "error")


    # ── Recover VOD ─────────────────────────────────────────────

    def _on_recover_vod(self):
        """Open the Deleted VOD Recovery Wizard dialog (F23)."""
        from ..recover_dialog import RecoverDialog
        dlg = RecoverDialog(self, log_fn=self._log)
        dlg.download_requested.connect(self._on_recover_download)
        dlg.exec()

    def _on_recover_download(self, url):
        """Handle a recovered VOD URL — paste into input and trigger fetch."""
        self.url_input.setText(url)
        self._on_fetch()


    # ── Batch URL import ────────────────────────────────────────



    # ── Finalize pipeline ───────────────────────────────────────


    def _on_trim_last(self):
        """Open the trim dialog for the most-recently-finished download."""
        out_dir = self._active_output_dir or self.output_input.text().strip()
        if not out_dir or not os.path.isdir(out_dir):
            self._set_status("No recent download folder to trim.", "warning")
            return
        self._open_clip_dialog_for_dir(out_dir)

    # Monitor actions → streamkeep.ui.tabs.monitor.MonitorTabMixin


    # ── Browser companion ───────────────────────────────────────

    def _on_companion_url(self, url, action):
        """The extension just POSTed a URL. Route it through the Fetch
        path or queue it immediately depending on action."""
        self._log(f"[COMPANION] Received {action.upper()} for {url[:80]}")
        self._present_main_window(0)
        try:
            self.url_input.setText(url)
        except Exception:
            pass
        if action == "queue":
            try:
                added = self._queue_add(url, title="", platform="")
                if added:
                    self._set_status(f"Queued via browser extension: {url[:80]}", "success")
                else:
                    self._set_status("That browser handoff is already in the queue.", "warning")
            except Exception as e:
                self._log(f"[COMPANION] Queue failed: {e}")
        else:
            self._on_fetch()

    def _on_companion_clip(self, url, start_secs, end_secs):
        """The extension sent validated clip bounds alongside a URL. Prefill the
        crop range and present the main window so the fetch that immediately
        follows (via ``url_received``) opens a ready-to-clip workflow once.

        The local server emits ``clip_received`` before ``url_received``, so the
        crop fields are populated before the fetch reads them at download time.
        """
        try:
            start = max(0.0, float(start_secs or 0.0))
            end = max(0.0, float(end_secs or 0.0))
        except (TypeError, ValueError):
            return
        self._present_main_window(0)
        try:
            if url:
                self.url_input.setText(url)
        except Exception:
            pass
        try:
            self.crop_start_input.setText(
                self._fmt_crop_time(start) if start > 0 else ""
            )
            self.crop_end_input.setText(
                self._fmt_crop_time(end) if end > 0 else ""
            )
        except Exception:
            pass
        span = ""
        if end > start:
            span = f" ({self._fmt_crop_time(start)}-{self._fmt_crop_time(end)})"
        self._log(f"[COMPANION] Clip range received{span}")
        self._set_status(
            "Browser clip range prefilled; fetching the source.", "info"
        )
