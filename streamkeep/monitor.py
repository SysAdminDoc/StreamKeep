"""Channel monitor — round-robin live detection + VOD subscriptions."""

import time

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .extractors import Extractor
from .models import MonitorEntry


class ChannelMonitor(QObject):
    """Polls channels for live status via round-robin."""

    status_changed = pyqtSignal()
    channel_went_live = pyqtSignal(str)      # channel_id
    new_vods_found = pyqtSignal(str, list)   # channel_id, list[VODInfo]
    log = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.entries = []
        self._poll_idx = 0
        self._timer = QTimer()
        self._timer.timeout.connect(self._poll_tick)
        self._timer.start(15_000)  # check one channel every 15s

    def add_channel(self, url, interval=120, auto_record=False, subscribe_vods=False):
        ext = Extractor.detect(url)
        if not ext:
            return False
        # Allow subscribe-only even if extractor doesn't support live check
        if not ext.supports_live_check() and not (
            subscribe_vods and ext.supports_vod_listing()
        ):
            return False
        ch_id = ext.extract_channel_id(url) or url
        for e in self.entries:
            if e.channel_id == ch_id:
                return False
        self.entries.append(MonitorEntry(
            url=url, platform=ext.NAME, channel_id=ch_id,
            interval_secs=interval, auto_record=auto_record,
            subscribe_vods=subscribe_vods,
        ))
        self.status_changed.emit()
        return True

    def remove_channel(self, idx):
        if 0 <= idx < len(self.entries):
            self.entries.pop(idx)
            self.status_changed.emit()

    def _poll_tick(self):
        # Snapshot entries so remove_channel during poll doesn't break indexing
        entries_snapshot = list(self.entries)
        if not entries_snapshot:
            return
        entry = entries_snapshot[self._poll_idx % len(entries_snapshot)]
        self._poll_idx += 1
        if entry not in self.entries:
            return
        now = time.time()
        if now - entry.last_check < entry.interval_secs:
            return
        entry.last_check = now
        ext = Extractor.detect(entry.url)
        if not ext:
            entry.last_status = "error"
            self.status_changed.emit()
            return
        try:
            if ext.supports_live_check():
                is_live = ext.check_live(entry.url)
                prev = entry.last_status
                entry.last_status = "live" if is_live else "offline"
                if is_live and prev != "live":
                    self.channel_went_live.emit(entry.channel_id)
                    self.log.emit(
                        f"[LIVE] {entry.platform}/{entry.channel_id} went live!"
                    )
            if entry.subscribe_vods and ext.supports_vod_listing():
                try:
                    vods = ext.list_vods(entry.url, log_fn=self.log.emit)
                    new_vods = []
                    for v in vods:
                        if v.source and v.source not in entry.archive_ids:
                            new_vods.append(v)
                    if new_vods:
                        for v in new_vods:
                            entry.archive_ids.append(v.source)
                        if len(entry.archive_ids) > 500:
                            entry.archive_ids = entry.archive_ids[-500:]
                        self.log.emit(
                            f"[SUBSCRIBE] {entry.channel_id}: "
                            f"{len(new_vods)} new VOD(s) found"
                        )
                        self.new_vods_found.emit(entry.channel_id, new_vods)
                except Exception as sub_ex:
                    self.log.emit(
                        f"[SUBSCRIBE ERROR] {entry.channel_id}: {sub_ex}"
                    )
            self.status_changed.emit()
        except Exception as ex:
            entry.last_status = "error"
            self.log.emit(f"[MONITOR ERROR] {entry.channel_id}: {ex}")
            self.status_changed.emit()

    def seed_archive(self, channel_id, vod_sources):
        """Populate the archive list for a channel (e.g. on first subscribe
        so we don't download the entire backlog)."""
        for e in self.entries:
            if e.channel_id == channel_id:
                for vs in vod_sources:
                    if vs and vs not in e.archive_ids:
                        e.archive_ids.append(vs)
                break

    def save_to_config(self, cfg):
        cfg["monitor_channels"] = [
            {
                "url": e.url, "interval": e.interval_secs,
                "auto_record": e.auto_record,
                "subscribe_vods": e.subscribe_vods,
                "archive_ids": list(e.archive_ids),
            }
            for e in self.entries
        ]

    def load_from_config(self, cfg):
        for ch in cfg.get("monitor_channels", []):
            ok = self.add_channel(
                ch["url"], ch.get("interval", 120),
                ch.get("auto_record", False),
                ch.get("subscribe_vods", False),
            )
            if ok:
                archive_ids = ch.get("archive_ids", [])
                if isinstance(archive_ids, list):
                    for e in self.entries:
                        if e.url == ch["url"]:
                            e.archive_ids = [str(x) for x in archive_ids if x]
                            break
