"""Durable queue execution for the headless REST service."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal

from . import db
from .config import write_log_line
from .utils import default_output_dir, fmt_size, safe_filename
from .workers import DownloadWorker, FetchWorker, FinalizeWorker


class HeadlessJobService(QObject):
    """Run persisted queue jobs through StreamKeep's fetch/download workers.

    Public methods may be called by the HTTP server thread. They only perform
    SQLite work there; Qt workers are created, cancelled, and observed on the
    service's owning thread via queued signals.
    """

    _wake_requested = pyqtSignal()
    _cancel_requested = pyqtSignal(str)

    def __init__(
        self,
        *,
        output_dir: str = "",
        max_concurrent: int = 2,
        parallel_connections: int = 4,
        config: dict[str, Any] | None = None,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self.output_dir = str(output_dir or default_output_dir())
        self.max_concurrent = max(1, int(max_concurrent or 1))
        self.parallel_connections = max(1, int(parallel_connections or 1))
        self.config = dict(config or {})
        self._apply_runtime_config()
        self._fetchers: dict[str, FetchWorker] = {}
        self._downloads: dict[str, DownloadWorker] = {}
        self._finalizers: dict[str, FinalizeWorker] = {}
        self._contexts: dict[str, dict[str, Any]] = {}
        self._download_errors: set[str] = set()
        self._last_progress: dict[str, int] = {}
        self._started = False
        self._stopping = False
        self._dispatch_timer = QTimer(self)
        self._dispatch_timer.setInterval(1000)
        self._dispatch_timer.timeout.connect(self._dispatch)
        self._wake_requested.connect(
            self._dispatch, Qt.ConnectionType.QueuedConnection
        )
        self._cancel_requested.connect(
            self._cancel_worker, Qt.ConnectionType.QueuedConnection
        )

    def start(self) -> int:
        """Recover interrupted work and begin dispatching eligible jobs."""
        db.init_db()
        recovered = db.recover_interrupted_queue_jobs()
        self._started = True
        self._stopping = False
        self._dispatch_timer.start()
        QTimer.singleShot(0, self._dispatch)
        return recovered

    def stop(self, wait_ms: int = 3000) -> None:
        """Stop workers without turning a service restart into job failure."""
        self._stopping = True
        self._started = False
        self._dispatch_timer.stop()
        for worker in list(self._fetchers.values()):
            worker.requestInterruption()
        for worker in list(self._downloads.values()):
            worker.cancel()
        for worker in list(self._finalizers.values()):
            worker.cancel()
        for worker in [
            *self._fetchers.values(), *self._downloads.values(),
            *self._finalizers.values(),
        ]:
            if worker.isRunning():
                worker.wait(max(0, int(wait_ms)))
        db.recover_interrupted_queue_jobs()
        self._fetchers.clear()
        self._downloads.clear()
        self._finalizers.clear()
        self._contexts.clear()
        self._download_errors.clear()
        self._last_progress.clear()

    # These provider methods are intentionally thread-safe for local_server.

    def enqueue(self, data: dict[str, Any] | str) -> dict[str, Any]:
        """Persist one acknowledged job, then wake the Qt dispatcher."""
        item = {"url": data} if isinstance(data, str) else dict(data)
        url = str(item.get("url", "")).strip()
        if not url.startswith(("http://", "https://")):
            raise ValueError("invalid url")
        item.update({
            "url": url,
            "title": str(item.get("title", "") or url),
            "quality": str(item.get("quality", "") or "best"),
            "output_dir": str(item.get("output_dir", "") or self.output_dir),
            "status": "queued",
            "source": str(item.get("source", "") or "headless-api"),
        })
        job = db.enqueue_queue_job(item)
        self._wake_requested.emit()
        return job

    def cancel(self, job_id: str) -> dict[str, Any] | None:
        """Persist cancellation and asynchronously stop an active worker."""
        job = db.cancel_queue_job(job_id)
        if job and job.get("status") == "cancelled":
            self._cancel_requested.emit(str(job_id))
        return job

    def retry_failure(self, failure_id: int) -> dict[str, Any] | None:
        """Return a persisted failed job to the executable queue."""
        failure = db.mark_failed_job_retrying(int(failure_id))
        if not failure:
            return None
        data = dict(failure.get("queue_data") or {})
        data.update({
            "url": str(data.get("url") or failure.get("url") or ""),
            "title": str(data.get("title") or failure.get("title") or ""),
            "platform": str(data.get("platform") or failure.get("platform") or ""),
            "output_dir": str(
                data.get("output_dir") or failure.get("output_dir") or self.output_dir
            ),
            "failure_id": int(failure_id),
            "status": "queued",
            "error": "",
        })
        job_id = str(data.get("job_id", ""))
        job = db.load_queue_job(job_id) if job_id else None
        if job:
            updates = dict(data)
            updates.pop("job_id", None)
            job = db.update_queue_job(job_id, **updates)
        else:
            job = db.enqueue_queue_job(data)
        self._wake_requested.emit()
        return job

    def discard_failure(self, failure_id: int) -> bool:
        failure = db.load_failed_job(int(failure_id))
        if not failure:
            return False
        db.mark_failed_job_discarded(int(failure_id))
        return True

    def state_snapshot(self) -> dict[str, Any]:
        """Return API state exclusively from durable SQLite records."""
        queue = db.load_queue()
        active = [
            item for item in queue
            if item.get("status") in {"fetching", "downloading", "finalizing"}
        ]
        return {
            "downloads": active,
            "queue": queue,
            "failures": db.load_failed_jobs(),
            "history": db.load_history(),
            "monitor": db.load_monitor_channels(),
            "live_channels": [],
            "active_workers": active,
            "resumable": [
                item for item in queue
                if item.get("status") in {"queued", "failed"}
            ],
        }

    def _dispatch(self) -> None:
        if not self._started or self._stopping:
            return
        available = self.max_concurrent - len(self._fetchers) - len(self._downloads)
        if available <= 0:
            return
        for job in db.load_queue_by_status("queued"):
            if available <= 0:
                break
            job_id = str(job.get("job_id", ""))
            if not job_id or job_id in self._fetchers or job_id in self._downloads:
                continue
            if not self._eligible(job):
                continue
            self._start_fetch(job)
            available -= 1

    @staticmethod
    def _eligible(job: dict[str, Any]) -> bool:
        start_at = str(job.get("start_at", "") or "").strip()
        if not start_at:
            return True
        try:
            target = datetime.fromisoformat(start_at.replace("Z", "+00:00"))
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            return target <= datetime.now(timezone.utc)
        except ValueError:
            return True

    def _start_fetch(self, job: dict[str, Any]) -> None:
        job_id = str(job["job_id"])
        current = db.update_queue_job(
            job_id, status="fetching", progress=0, error=""
        )
        if not current:
            return
        worker = FetchWorker(str(current.get("url", "")))
        self._bind_fetcher(job_id, worker)
        write_log_line(f"[SERVICE] Fetching job {job_id}: {current.get('url', '')}")
        worker.start()

    def _bind_fetcher(self, job_id: str, worker: FetchWorker) -> None:
        self._fetchers[job_id] = worker
        worker.finished.connect(
            lambda info, jid=job_id: self._on_fetch_done(jid, info)
        )
        worker.error.connect(
            lambda error, jid=job_id: self._on_fetch_error(jid, error)
        )
        worker.vods_found.connect(
            lambda vods, platform, _cursor, jid=job_id:
            self._on_vods_found(jid, vods, platform)
        )
        worker.log.connect(write_log_line)

    def _on_vods_found(self, job_id: str, vods: list[Any], platform: str) -> None:
        previous = self._fetchers.pop(job_id, None)
        if previous and previous.isRunning():
            previous.wait(500)
        if not vods:
            self._fail_job(job_id, "fetch", "No VODs found for this URL")
            return
        chosen = vods[0]
        job = db.load_queue_job(job_id)
        if not job or job.get("status") == "cancelled":
            self._dispatch()
            return
        worker = FetchWorker(
            str(job.get("url", "")),
            vod_source=getattr(chosen, "source", ""),
            vod_platform=getattr(chosen, "platform", "") or platform,
            vod_title=getattr(chosen, "title", ""),
            vod_channel=getattr(chosen, "channel", ""),
        )
        self._bind_fetcher(job_id, worker)
        worker.start()

    def _on_fetch_done(self, job_id: str, info: Any) -> None:
        worker = self._fetchers.pop(job_id, None)
        if worker and worker.isRunning():
            worker.wait(500)
        job = db.load_queue_job(job_id)
        if not job or job.get("status") == "cancelled" or self._stopping:
            self._dispatch()
            return
        quality = self._pick_quality(getattr(info, "qualities", []), job.get("quality", "best"))
        if quality is None and not getattr(info, "url", ""):
            self._fail_job(job_id, "fetch", "No playable quality", info=info)
            return

        playlist_url = quality.url if quality else info.url
        format_type = quality.format_type if quality else "hls"
        title = str(getattr(info, "title", "") or job.get("title") or "download")
        output_dir = str(job.get("output_dir", "") or self.output_dir)
        try:
            os.makedirs(output_dir, exist_ok=True)
        except OSError as error:
            self._fail_job(
                job_id, "download", f"Cannot create output directory: {error}",
                info=info, output_dir=output_dir,
            )
            return

        is_live = bool(getattr(info, "is_live", False)) or float(
            getattr(info, "total_secs", 0) or 0
        ) <= 0
        segments = [(
            0, safe_filename(title), 0,
            0 if is_live else int(getattr(info, "total_secs", 0) or 0),
        )]
        worker = DownloadWorker(playlist_url or "", segments, output_dir, format_type)
        if quality:
            worker.audio_url = quality.audio_url
            worker.ytdlp_source = quality.ytdlp_source
            worker.ytdlp_format = quality.ytdlp_format
        worker.parallel_connections = self.parallel_connections
        worker.cookies_browser = str(self.config.get("cookies_browser", "") or "")
        worker.rate_limit = str(self.config.get("rate_limit", "") or "")
        worker.proxy = str(self.config.get("proxy", "") or "")
        worker.download_subs = bool(self.config.get("download_subs", False))
        worker.subtitle_languages = str(
            self.config.get("subtitle_languages", "en.*,en") or ""
        )
        worker.subtitle_auto = bool(self.config.get("subtitle_auto", True))
        worker.subtitle_convert = str(
            self.config.get("subtitle_convert", "") or ""
        )
        worker.subtitle_embed = bool(self.config.get("subtitle_embed", True))
        worker.sponsorblock = bool(self.config.get("sponsorblock", False))
        if bool(self.config.get("chunk_long_captures", False)):
            try:
                worker.chunk_length_secs = max(
                    60, int(self.config.get("chunk_length_secs", 7200) or 7200)
                )
            except (TypeError, ValueError):
                worker.chunk_length_secs = 7200
        clip_start = self._float_or_none(job.get("clip_start"))
        clip_end = self._float_or_none(job.get("clip_end"))
        if clip_start is not None and clip_end is not None and clip_end > clip_start:
            worker.segments = [(
                0, safe_filename(title), clip_start, clip_end - clip_start,
            )]
            worker.download_sections = f"*{clip_start}-{clip_end}"
        worker.log.connect(write_log_line)
        worker.progress.connect(
            lambda _idx, percent, text, jid=job_id:
            self._on_progress(jid, percent, text)
        )
        worker.error.connect(
            lambda _idx, error, jid=job_id:
            self._on_download_error(jid, error)
        )
        worker.all_done.connect(
            lambda jid=job_id: self._on_download_done(jid)
        )
        worker.finished.connect(
            lambda jid=job_id: self._on_download_finished(jid)
        )
        self._contexts[job_id] = {
            "info": info,
            "output_dir": output_dir,
            "quality": str(getattr(quality, "name", "") or job.get("quality", "")),
        }
        self._downloads[job_id] = worker
        db.update_queue_job(
            job_id,
            status="downloading",
            progress=0,
            title=title,
            platform=str(getattr(info, "platform", "") or ""),
            output_dir=output_dir,
        )
        write_log_line(f"[SERVICE] Downloading job {job_id}: {title}")
        worker.start()

    def _on_fetch_error(self, job_id: str, error: str) -> None:
        worker = self._fetchers.pop(job_id, None)
        if worker and worker.isRunning():
            worker.wait(500)
        job = db.load_queue_job(job_id)
        if self._stopping or not job or job.get("status") == "cancelled":
            self._dispatch()
            return
        self._fail_job(job_id, "fetch", error)

    def _on_progress(self, job_id: str, percent: int, text: str) -> None:
        value = max(0, min(100, int(percent or 0)))
        previous = self._last_progress.get(job_id, -5)
        if value < 100 and value - previous < 5:
            return
        self._last_progress[job_id] = value
        job = db.load_queue_job(job_id)
        if job and job.get("status") == "downloading":
            db.update_queue_job(job_id, progress=value, progress_text=str(text or ""))

    def _on_download_error(self, job_id: str, error: str) -> None:
        if job_id in self._download_errors:
            return
        self._download_errors.add(job_id)
        job = db.load_queue_job(job_id)
        if self._stopping or not job or job.get("status") == "cancelled":
            return
        ctx = self._contexts.get(job_id, {})
        self._fail_job(
            job_id, "download", error,
            info=ctx.get("info"), output_dir=str(ctx.get("output_dir", "")),
            dispatch=False,
        )

    def _on_download_done(self, job_id: str) -> None:
        job = db.load_queue_job(job_id)
        if (
            self._stopping or job_id in self._download_errors or not job
            or job.get("status") != "downloading"
        ):
            return
        ctx = self._contexts.get(job_id, {})
        info = ctx.get("info")
        output_dir = str(ctx.get("output_dir", "") or self.output_dir)
        db.update_queue_job(
            job_id, status="finalizing", progress=100,
            progress_text="Saving metadata and integrity manifest",
            output_dir=output_dir,
        )
        finalizer = FinalizeWorker({
            "out_dir": output_dir,
            "quality_name": str(ctx.get("quality", "")),
            "history_url": str(job.get("url", "")),
            "info": info,
            "file_base": safe_filename(str(getattr(info, "title", "") or "")),
            "write_nfo": bool(self.config.get("write_nfo", False)),
            "download_chat": bool(self.config.get("download_twitch_chat", False)),
            "postprocess_snapshot": self._postprocess_snapshot(),
            "record_manifest": True,
            "platform": str(getattr(info, "platform", "") or job.get("platform", "")),
            "channel": str(getattr(info, "channel", "") or ""),
            "title": str(getattr(info, "title", "") or job.get("title", "")),
        })
        finalizer.log.connect(write_log_line)
        finalizer.progress.connect(
            lambda label, step, total, jid=job_id:
            self._on_finalize_progress(jid, label, step, total)
        )
        finalizer.done.connect(
            lambda result, jid=job_id: self._on_finalize_done(jid, result)
        )
        finalizer.finished.connect(
            lambda jid=job_id: self._on_finalize_finished(jid)
        )
        self._finalizers[job_id] = finalizer
        finalizer.start()

    def _on_download_finished(self, job_id: str) -> None:
        self._downloads.pop(job_id, None)
        if job_id not in self._finalizers:
            self._contexts.pop(job_id, None)
        self._download_errors.discard(job_id)
        self._last_progress.pop(job_id, None)
        self._dispatch()

    def _on_finalize_progress(
        self, job_id: str, label: str, step: int, total: int
    ) -> None:
        job = db.load_queue_job(job_id)
        if job and job.get("status") == "finalizing":
            suffix = f" ({int(step)}/{int(total)})" if total else ""
            db.update_queue_job(
                job_id, progress_text=f"{str(label or 'Finalizing')}{suffix}"
            )

    def _on_finalize_done(self, job_id: str, result: dict[str, Any]) -> None:
        job = db.load_queue_job(job_id)
        if self._stopping or not job or job.get("status") == "cancelled":
            return
        if result.get("cancelled"):
            return
        output_dir = str(result.get("out_dir", "") or self.output_dir)
        size_label = str(result.get("size_label", "") or "")
        if not size_label:
            size_label = fmt_size(self._folder_size(output_dir))
        history_id = db.save_history_entry({
            "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "platform": str(result.get("platform", "") or job.get("platform", "")),
            "title": str(result.get("title", "") or job.get("title", "")),
            "channel": str(result.get("channel", "") or ""),
            "quality": str(result.get("quality_name", "") or ""),
            "size": size_label,
            "path": output_dir,
            "url": str(result.get("history_url", "") or job.get("url", "")),
        })
        manifest = result.get("archive_manifest")
        if history_id and isinstance(manifest, dict):
            db.save_archive_manifest(
                int(history_id), output_dir, manifest, status="created",
                details=f"Captured {len(manifest.get('files', []) or [])} file(s)",
            )
        warning = str(
            result.get("finalize_error") or result.get("archive_manifest_error") or ""
        )
        warning_failure_id = 0
        if warning:
            warning_failure_id = db.save_failed_job(
                url=str(job.get("url", "")),
                platform=str(result.get("platform", "") or job.get("platform", "")),
                title=str(result.get("title", "") or job.get("title", "")),
                stage="finalize", error=warning, output_dir=output_dir,
                queue_data=job,
                context={"job_id": job_id, "service": "headless"},
            )
        previous_failure_id = int(job.get("failure_id", 0) or 0)
        if previous_failure_id and previous_failure_id != warning_failure_id:
            db.mark_failed_job_resolved(previous_failure_id)
        if not warning:
            db.mark_failed_jobs_resolved_for_url(str(job.get("url", "")))
        db.update_queue_job(
            job_id, status="done", progress=100,
            progress_text="Complete" if not warning else "Complete with finalization warning",
            completed_at=datetime.now(timezone.utc).isoformat(),
            history_id=int(history_id or 0), output_dir=output_dir,
            finalize_error=warning, failure_id=warning_failure_id,
        )
        write_log_line(f"[SERVICE] Completed job {job_id}")

    def _on_finalize_finished(self, job_id: str) -> None:
        self._finalizers.pop(job_id, None)
        self._contexts.pop(job_id, None)
        self._dispatch()

    def _fail_job(
        self,
        job_id: str,
        stage: str,
        error: str,
        *,
        info: Any = None,
        output_dir: str = "",
        dispatch: bool = True,
    ) -> None:
        job = db.load_queue_job(job_id)
        if not job or job.get("status") == "cancelled" or self._stopping:
            if dispatch:
                self._dispatch()
            return
        output_dir = str(output_dir or job.get("output_dir", "") or self.output_dir)
        resume_sidecar = os.path.join(output_dir, ".streamkeep_resume.json")
        failure_id = db.save_failed_job(
            url=str(job.get("url", "")),
            platform=str(getattr(info, "platform", "") or job.get("platform", "")),
            title=str(getattr(info, "title", "") or job.get("title", "")),
            stage=stage,
            error=str(error or "Unknown error"),
            output_dir=output_dir,
            resume_sidecar=resume_sidecar if os.path.isfile(resume_sidecar) else "",
            queue_data=job,
            context={"job_id": job_id, "service": "headless"},
        )
        db.update_queue_job(
            job_id, status="failed", error=str(error or "Unknown error"),
            failure_id=failure_id, failed_at=datetime.now(timezone.utc).isoformat(),
        )
        write_log_line(f"[SERVICE] Job {job_id} failed during {stage}: {error}")
        if dispatch:
            self._dispatch()

    def _cancel_worker(self, job_id: str) -> None:
        fetcher = self._fetchers.get(job_id)
        if fetcher:
            fetcher.requestInterruption()
            QTimer.singleShot(50, lambda jid=job_id: self._reap_cancelled_fetch(jid))
        download = self._downloads.get(job_id)
        if download:
            download.cancel()
        finalizer = self._finalizers.get(job_id)
        if finalizer:
            finalizer.cancel()
        if not fetcher and not download and not finalizer:
            self._dispatch()

    def _reap_cancelled_fetch(self, job_id: str) -> None:
        fetcher = self._fetchers.get(job_id)
        if fetcher and fetcher.isRunning():
            QTimer.singleShot(50, lambda jid=job_id: self._reap_cancelled_fetch(jid))
            return
        self._fetchers.pop(job_id, None)
        self._dispatch()

    @staticmethod
    def _pick_quality(qualities: list[Any], preference: Any) -> Any:
        if not qualities:
            return None
        pref = str(preference or "best").lower().strip()
        if pref in {"best", "source", "highest", ""}:
            return qualities[0]
        if pref == "lowest":
            return qualities[-1]
        for quality in qualities:
            if (
                pref in str(getattr(quality, "name", "")).lower()
                or pref in str(getattr(quality, "resolution", "")).lower()
            ):
                return quality
        return qualities[0]

    @staticmethod
    def _folder_size(path: str) -> int:
        total = 0
        try:
            for entry in Path(path).rglob("*"):
                if entry.is_file():
                    total += entry.stat().st_size
        except OSError:
            pass
        return total

    @staticmethod
    def _float_or_none(value: Any) -> float | None:
        try:
            return float(value) if value is not None and value != "" else None
        except (TypeError, ValueError):
            return None

    def _postprocess_snapshot(self) -> dict[str, Any]:
        names = (
            "extract_audio", "normalize_loudness", "reencode_h265",
            "contact_sheet", "split_by_chapter", "convert_video",
            "convert_video_format", "convert_video_codec", "convert_video_scale",
            "convert_video_fps", "convert_audio", "convert_audio_format",
            "convert_audio_codec", "convert_audio_bitrate",
            "convert_audio_samplerate", "convert_delete_source",
        )
        snapshot: dict[str, Any] = {}
        for name in names:
            key = f"pp_{name}"
            if key in self.config:
                snapshot[name] = self.config[key]
        return snapshot

    def _apply_runtime_config(self) -> None:
        from .extractors.ytdlp import YtDlpExtractor
        from .http import set_native_proxy

        YtDlpExtractor.cookies_browser = str(
            self.config.get("cookies_browser", "") or ""
        )
        YtDlpExtractor.rate_limit = str(self.config.get("rate_limit", "") or "")
        YtDlpExtractor.proxy = str(self.config.get("proxy", "") or "")
        YtDlpExtractor.download_subs = bool(
            self.config.get("download_subs", False)
        )
        YtDlpExtractor.subtitle_languages = str(
            self.config.get("subtitle_languages", "en.*,en") or ""
        )
        YtDlpExtractor.subtitle_auto = bool(
            self.config.get("subtitle_auto", True)
        )
        YtDlpExtractor.subtitle_convert = str(
            self.config.get("subtitle_convert", "") or ""
        )
        YtDlpExtractor.subtitle_embed = bool(
            self.config.get("subtitle_embed", True)
        )
        YtDlpExtractor.sponsorblock = bool(self.config.get("sponsorblock", False))
        set_native_proxy(YtDlpExtractor.proxy)
