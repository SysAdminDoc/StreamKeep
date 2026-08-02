"""Durable queue execution for the headless REST service."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal

from . import db
from .config import write_log_line
from .models import default_media_tracks
from .retry import sanitize_failure_reason
from .upgrade import (
    UpgradeSafetyError,
    identity_matches,
    plan_upgrade_paths,
    prepare_upgrade_staging,
    quality_rank,
)
from .utils import default_output_dir, fmt_size, safe_filename
from .workers import (
    DownloadWorker,
    FetchWorker,
    FinalizeWorker,
    ScheduledBackupWorker,
)


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
        owner_id: str = "",
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self.output_dir = str(output_dir or default_output_dir())
        self.max_concurrent = max(1, int(max_concurrent or 1))
        self.parallel_connections = max(1, int(parallel_connections or 1))
        self.config = dict(config or {})
        self.owner_id = str(owner_id or f"server:{os.getpid()}:{uuid.uuid4().hex}")
        self._lease_acquired = False
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
        self._lease_timer = QTimer(self)
        self._lease_timer.setInterval(10_000)
        self._lease_timer.timeout.connect(self._heartbeat_lease)
        self._backup_worker: ScheduledBackupWorker | None = None
        self._backup_timer = QTimer(self)
        self._backup_timer.setInterval(60_000)
        self._backup_timer.timeout.connect(self._tick_scheduled_backup)
        self._wake_requested.connect(
            self._dispatch, Qt.ConnectionType.QueuedConnection
        )
        self._cancel_requested.connect(
            self._cancel_worker, Qt.ConnectionType.QueuedConnection
        )

    def start(self) -> int:
        """Recover interrupted work and begin dispatching eligible jobs."""
        db.init_db()
        lease = db.acquire_executor_lease(
            self.owner_id, owner_kind="headless server",
        )
        if not lease.get("acquired"):
            raise RuntimeError(str(lease.get("message") or "Queue executor is busy"))
        recovered = int(lease.get("recovered", 0) or 0)
        self._lease_acquired = True
        self._started = True
        self._stopping = False
        self._dispatch_timer.start()
        self._lease_timer.start()
        self._backup_timer.start()
        QTimer.singleShot(0, self._dispatch)
        QTimer.singleShot(0, self._tick_scheduled_backup)
        return recovered

    def stop(self, wait_ms: int = 3000) -> None:
        """Stop workers without turning a service restart into job failure."""
        self._stopping = True
        self._started = False
        self._dispatch_timer.stop()
        self._lease_timer.stop()
        self._backup_timer.stop()
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
        if self._backup_worker is not None and self._backup_worker.isRunning():
            # A backup is a short, self-contained write; let it finish so the
            # claim is released and no partial archive is left behind.
            self._backup_worker.wait(max(0, int(wait_ms)))
            db.release_backup_claim(self.owner_id)
        self._backup_worker = None
        if self._lease_acquired:
            db.release_executor_lease(self.owner_id)
            self._lease_acquired = False
        self._fetchers.clear()
        self._downloads.clear()
        self._finalizers.clear()
        self._contexts.clear()
        self._download_errors.clear()
        self._last_progress.clear()

    def _heartbeat_lease(self) -> None:
        if not self._started or self._stopping or not self._lease_acquired:
            return
        if db.heartbeat_executor_lease(self.owner_id):
            return
        self._lease_acquired = False
        self._started = False
        self._stopping = True
        self._dispatch_timer.stop()
        self._lease_timer.stop()
        for worker in list(self._fetchers.values()):
            worker.requestInterruption()
        for worker in list(self._downloads.values()):
            worker.cancel()
        for worker in list(self._finalizers.values()):
            worker.cancel()
        write_log_line(
            "[SERVICE] Queue executor lease was lost; active work was stopped "
            "to prevent duplicate execution."
        )

    def _tick_scheduled_backup(self) -> None:
        """Start one due rotating backup while this process owns execution."""
        if not self._started or self._stopping or not self._lease_acquired:
            return
        if self._backup_worker is not None and self._backup_worker.isRunning():
            return
        worker = ScheduledBackupWorker(self.config, self.owner_id, parent=self)
        worker.finished_run.connect(self._on_backup_finished)
        worker.finished.connect(self._clear_backup_worker)
        worker.finished.connect(worker.deleteLater)
        self._backup_worker = worker
        worker.start()

    def _clear_backup_worker(self) -> None:
        self._backup_worker = None

    def _on_backup_finished(
        self, ok: bool, message: str, _state: dict[str, Any],
    ) -> None:
        write_log_line(
            message or ("[BACKUP] Automatic backup completed"
                        if ok else "[BACKUP] Automatic backup failed")
        )

    # These provider methods are intentionally thread-safe for local_server.

    def enqueue(self, data: dict[str, Any] | str) -> dict[str, Any]:
        """Persist one acknowledged job, then wake the Qt dispatcher."""
        item = {"url": data} if isinstance(data, str) else dict(data)
        url = str(item.get("url", "")).strip()
        if not url.startswith(("http://", "https://")):
            raise ValueError("invalid url")
        # Ordered rules engine (V15): fold matching folder/template/preset/
        # proxy/priority/auto-start actions into the job before defaults are
        # applied, so a rule-set output_dir wins over the service default.
        from .rules import apply_rules_to_job
        item = apply_rules_to_job(item, self.config)
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
        job = db.promote_failed_job_retry(int(failure_id))
        if job:
            self._wake_requested.emit()
        return job

    def cancel_failure_retry(self, failure_id: int) -> bool:
        """Disable a scheduled retry without discarding its failure record."""
        return db.cancel_failed_job_retry(int(failure_id))

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
            "failures": [
                db.failed_job_public_view(item) for item in db.load_failed_jobs()
            ],
            "retry_circuits": db.load_retry_circuits(),
            "backup": db.backup_state_public_view(db.load_backup_state()),
            "history": db.query_history_page(limit=100),
            "history_total": db.history_count(),
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
        promoted = db.promote_due_failed_jobs(self.owner_id)
        for job in promoted:
            write_log_line(
                "[SERVICE] Scheduled automatic retry "
                f"{job.get('job_id', '')} for {job.get('platform', '') or 'source'}"
            )
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
        current = db.claim_queue_job(
            job_id, self.owner_id, status="fetching", progress=0, error=""
        )
        if not current:
            return
        worker = FetchWorker(
            str(current.get("url", "")),
            vod_source=current.get("vod_source") or None,
            vod_platform=current.get("vod_platform") or None,
            vod_title=current.get("vod_title") or None,
            vod_channel=current.get("vod_channel") or None,
            source_id=current.get("source_id") or None,
            webpage_url=current.get("webpage_url") or None,
        )
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
        if (
            not job
            or job.get("status") != "fetching"
            or job.get("execution_owner") != self.owner_id
        ):
            self._dispatch()
            return
        worker = FetchWorker(
            str(job.get("url", "")),
            vod_source=getattr(chosen, "source", ""),
            vod_platform=getattr(chosen, "platform", "") or platform,
            vod_title=getattr(chosen, "title", ""),
            vod_channel=getattr(chosen, "channel", ""),
            source_id=getattr(chosen, "source_id", ""),
            webpage_url=getattr(chosen, "webpage_url", ""),
        )
        self._bind_fetcher(job_id, worker)
        worker.start()

    def _on_fetch_done(self, job_id: str, info: Any) -> None:
        worker = self._fetchers.pop(job_id, None)
        if worker and worker.isRunning():
            worker.wait(500)
        job = db.load_queue_job(job_id)
        if (
            not job
            or job.get("status") != "fetching"
            or job.get("execution_owner") != self.owner_id
            or self._stopping
        ):
            self._dispatch()
            return
        quality = self._pick_quality(getattr(info, "qualities", []), job.get("quality", "best"))
        if quality is None and not getattr(info, "url", ""):
            self._fail_job(job_id, "fetch", "No playable quality", info=info)
            return

        playlist_url = quality.url if quality else info.url
        format_type = quality.format_type if quality else "hls"
        title = str(getattr(info, "title", "") or job.get("title") or "download")
        resolved_platform = str(getattr(info, "platform", "") or "")
        resolved_source_id = str(getattr(info, "source_id", "") or "")
        chosen_quality = str(
            (
                getattr(quality, "resolution", "")
                or getattr(quality, "name", "")
            )
            if quality else "Best available"
        )
        upgrade_paths = None
        existing_history = None
        if bool(job.get("is_upgrade", False)):
            if not identity_matches(
                str(job.get("vod_platform") or job.get("platform") or ""),
                str(job.get("source_id", "") or ""),
                resolved_platform,
                resolved_source_id,
            ):
                self._fail_job(
                    job_id,
                    "fetch",
                    "Resolved media identity does not match the queued "
                    "upgrade candidate",
                    info=info,
                )
                return
            existing_history = db.find_history_by_identity(
                resolved_platform, resolved_source_id,
            )
            if (
                not existing_history
                or not existing_history.get("path")
                or not os.path.isdir(str(existing_history.get("path")))
            ):
                self._fail_job(
                    job_id,
                    "fetch",
                    "Known-good recording for this upgrade is missing",
                    info=info,
                )
                return
            if quality_rank(chosen_quality) <= quality_rank(
                str(existing_history.get("quality", ""))
            ):
                db.transition_owned_queue_job(
                    job_id,
                    self.owner_id,
                    expected_statuses="fetching",
                    status="done",
                    progress=100,
                    progress_text=(
                        f"No upgrade: {chosen_quality} is not higher than "
                        f"{existing_history.get('quality', '')}"
                    ),
                )
                self._dispatch()
                return
            minimum = str(job.get("upgrade_min_quality", "") or "")
            if minimum and quality_rank(chosen_quality) < quality_rank(minimum):
                db.transition_owned_queue_job(
                    job_id,
                    self.owner_id,
                    expected_statuses="fetching",
                    status="done",
                    progress=100,
                    progress_text=(
                        f"No upgrade: {chosen_quality} is below {minimum}"
                    ),
                )
                self._dispatch()
                return
            try:
                upgrade_paths = plan_upgrade_paths(
                    str(existing_history.get("path")),
                    job_id,
                    chosen_quality,
                )
                prepare_upgrade_staging(upgrade_paths)
            except (OSError, UpgradeSafetyError) as error:
                self._fail_job(
                    job_id, "download", str(error), info=info,
                )
                return
            output_dir = str(upgrade_paths.staging)
        else:
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
        from .download_options import (
            resolve_external_downloader_options,
            resolve_ytdlp_arg_template, resolve_ytdlp_transfer_options,
        )
        from .job_spec import DownloadJobSpec

        template_name = str(job.get("arg_template", "") or "")
        if template_name and format_type != "ytdlp_direct":
            self._fail_job(
                job_id, "download",
                "yt-dlp argument templates require a yt-dlp direct source",
                info=info, output_dir=output_dir,
            )
            return
        try:
            ytdlp_template_args = resolve_ytdlp_arg_template(
                self.config.get("ytdlp_arg_templates", {}), template_name,
            )
        except ValueError as error:
            self._fail_job(
                job_id, "download", str(error), info=info,
                output_dir=output_dir,
            )
            return

        transfer = resolve_ytdlp_transfer_options(self.config)
        ext_dl = resolve_external_downloader_options(self.config)
        chunk_secs = 0
        if bool(self.config.get("chunk_long_captures", False)):
            try:
                chunk_secs = max(
                    60, int(self.config.get("chunk_length_secs", 7200) or 7200)
                )
            except (TypeError, ValueError):
                chunk_secs = 7200

        final_segments = list(segments)
        download_sections = ""
        clip_start = self._float_or_none(job.get("clip_start"))
        clip_end = self._float_or_none(job.get("clip_end"))
        if clip_start is not None and clip_end is not None and clip_end > clip_start:
            final_segments = [(
                0, safe_filename(title), clip_start, clip_end - clip_start,
            )]
            download_sections = f"*{clip_start}-{clip_end}"

        spec = DownloadJobSpec(
            source_platform=str(getattr(info, "platform", "") or ""),
            source_id=str(getattr(info, "source_id", "") or ""),
            webpage_url=str(getattr(info, "webpage_url", "") or ""),
            playlist_url=playlist_url or "",
            segments=tuple(tuple(s) for s in final_segments),
            output_dir=output_dir,
            format_type=format_type,
            audio_url=quality.audio_url if quality else "",
            selected_tracks=tuple(default_media_tracks(quality) if quality else []),
            ytdlp_source=quality.ytdlp_source if quality else "",
            ytdlp_format=quality.ytdlp_format if quality else "",
            parallel_connections=self.parallel_connections,
            cookies_browser=str(self.config.get("cookies_browser", "") or ""),
            rate_limit=str(self.config.get("rate_limit", "") or ""),
            proxy=str(job.get("proxy", "") or self.config.get("proxy", "") or ""),
            download_subs=bool(self.config.get("download_subs", False)),
            capture_youtube_chat=bool(self.config.get("capture_youtube_chat", False)),
            subtitle_languages=str(self.config.get("subtitle_languages", "en.*,en") or ""),
            subtitle_auto=bool(self.config.get("subtitle_auto", True)),
            subtitle_convert=str(self.config.get("subtitle_convert", "") or ""),
            subtitle_embed=bool(self.config.get("subtitle_embed", True)),
            sponsorblock=bool(self.config.get("sponsorblock", False)),
            sponsorblock_mark=str(self.config.get("sponsorblock_mark", "") or ""),
            sponsorblock_remove=str(self.config.get("sponsorblock_remove", "sponsor,selfpromo,interaction") or ""),
            sponsorblock_api=str(self.config.get("sponsorblock_api", "") or ""),
            ytdlp_concurrent_fragments=transfer.get("concurrent_fragments", 0),
            ytdlp_retries=transfer.get("retries", ""),
            ytdlp_fragment_retries=transfer.get("fragment_retries", ""),
            ytdlp_retry_sleep=transfer.get("retry_sleep", ""),
            ytdlp_unavailable_fragments=transfer.get("unavailable_fragments", ""),
            ytdlp_throttled_rate=transfer.get("throttled_rate", ""),
            ytdlp_live_from_start=transfer.get("live_from_start", False),
            ytdlp_wait_for_video=transfer.get("wait_for_video", ""),
            ytdlp_embed_chapters=transfer.get("embed_chapters"),
            ytdlp_embed_metadata=transfer.get("embed_metadata"),
            ytdlp_embed_thumbnail=transfer.get("embed_thumbnail"),
            ytdlp_external_downloader=str(ext_dl.get("external_downloader", "") or ""),
            ytdlp_aria2c_connections=int(ext_dl.get("aria2c_connections", 0) or 0),
            ytdlp_aria2c_splits=int(ext_dl.get("aria2c_splits", 0) or 0),
            ytdlp_aria2c_min_split_size=str(ext_dl.get("aria2c_min_split_size", "") or ""),
            ytdlp_template_name=template_name,
            ytdlp_template_args=tuple(ytdlp_template_args),
            chunk_length_secs=chunk_secs,
            download_sections=download_sections,
            download_archive=str(job.get("download_archive", "") or ""),
            break_on_existing=bool(job.get("break_on_existing", False)),
        )
        worker = DownloadWorker.from_spec(spec)
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
            "quality": chosen_quality,
            "is_upgrade": bool(job.get("is_upgrade", False)),
            "upgrade_existing_path": (
                str(upgrade_paths.existing) if upgrade_paths else ""
            ),
            "upgrade_final_dir": (
                str(upgrade_paths.final) if upgrade_paths else ""
            ),
        }
        self._downloads[job_id] = worker
        current = db.transition_owned_queue_job(
            job_id,
            self.owner_id,
            expected_statuses="fetching",
            status="downloading",
            progress=0,
            title=title,
            platform=resolved_platform,
            source_id=resolved_source_id,
            webpage_url=str(getattr(info, "webpage_url", "") or ""),
            output_dir=output_dir,
            upgrade_history_id=(
                int(existing_history.get("id", 0) or 0)
                if existing_history else 0
            ),
            upgrade_existing_path=(
                str(upgrade_paths.existing) if upgrade_paths else ""
            ),
            upgrade_stage_dir=(
                str(upgrade_paths.staging) if upgrade_paths else ""
            ),
            upgrade_final_dir=(
                str(upgrade_paths.final) if upgrade_paths else ""
            ),
        )
        if not current:
            self._contexts.pop(job_id, None)
            self._downloads.pop(job_id, None)
            self._dispatch()
            return
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
        if (
            job
            and job.get("status") == "downloading"
            and job.get("execution_owner") == self.owner_id
        ):
            db.transition_owned_queue_job(
                job_id, self.owner_id, expected_statuses="downloading",
                progress=value, progress_text=str(text or ""),
            )

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
            or job.get("execution_owner") != self.owner_id
        ):
            return
        ctx = self._contexts.get(job_id, {})
        info = ctx.get("info")
        output_dir = str(ctx.get("output_dir", "") or self.output_dir)
        current = db.transition_owned_queue_job(
            job_id, self.owner_id, expected_statuses="downloading",
            status="finalizing", progress=100,
            progress_text="Saving metadata and integrity manifest",
            output_dir=output_dir,
        )
        if not current:
            return
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
            "source_id": str(getattr(info, "source_id", "") or ""),
            "queue_job_id": job_id,
            "is_upgrade": bool(ctx.get("is_upgrade", False)),
            "upgrade_existing_path": str(
                ctx.get("upgrade_existing_path", "") or ""
            ),
            "upgrade_final_dir": str(
                ctx.get("upgrade_final_dir", "") or ""
            ),
            "expected_duration": float(
                getattr(info, "total_secs", 0) or 0
            ),
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
        if (
            job
            and job.get("status") == "finalizing"
            and job.get("execution_owner") == self.owner_id
        ):
            suffix = f" ({int(step)}/{int(total)})" if total else ""
            db.transition_owned_queue_job(
                job_id, self.owner_id, expected_statuses="finalizing",
                progress_text=f"{str(label or 'Finalizing')}{suffix}"
            )

    def _on_finalize_done(self, job_id: str, result: dict[str, Any]) -> None:
        job = db.load_queue_job(job_id)
        if (
            self._stopping
            or not job
            or job.get("status") != "finalizing"
            or job.get("execution_owner") != self.owner_id
        ):
            return
        if result.get("cancelled"):
            return
        output_dir = str(result.get("out_dir", "") or self.output_dir)
        failure = str(
            result.get("finalize_error")
            or result.get("archive_manifest_error")
            or ""
        )
        if result.get("is_upgrade") and not result.get("upgrade_activated"):
            failure = failure or "Upgrade activation did not complete"
        if failure:
            safe_failure = sanitize_failure_reason(failure)
            failure_id = db.save_failed_job(
                url=str(job.get("url", "")),
                platform=str(
                    result.get("platform", "") or job.get("platform", "")
                ),
                title=str(result.get("title", "") or job.get("title", "")),
                stage="finalize",
                error=failure,
                output_dir=output_dir,
                queue_data=job,
                context={"job_id": job_id, "service": "headless"},
            )
            db.transition_owned_queue_job(
                job_id,
                self.owner_id,
                expected_statuses="finalizing",
                status="failed",
                progress_text=safe_failure[:240],
                finalize_error=safe_failure,
                failure_id=int(failure_id or 0),
            )
            write_log_line(
                f"[SERVICE] Finalization failed for {job_id}: {safe_failure}"
            )
            return
        size_label = str(result.get("size_label", "") or "")
        if not size_label:
            size_label = fmt_size(self._folder_size(output_dir))
        try:
            manifest = result.get("archive_manifest")
            history_id = db.save_completed_recording({
                "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "platform": str(
                    result.get("platform", "") or job.get("platform", "")
                ),
                "source_id": str(
                    result.get("source_id", "") or job.get("source_id", "")
                ),
                "title": str(result.get("title", "") or job.get("title", "")),
                "channel": str(result.get("channel", "") or ""),
                "quality": str(result.get("quality_name", "") or ""),
                "size": size_label,
                "path": output_dir,
                "url": str(
                    result.get("history_url", "") or job.get("url", "")
                ),
            }, manifest if isinstance(manifest, dict) else None)
        except Exception as error:
            self._fail_job(
                job_id,
                "finalize",
                f"Could not persist completed recording: {error}",
                info=result.get("info"),
                output_dir=output_dir,
                dispatch=False,
            )
            return
        previous_failure_id = int(job.get("failure_id", 0) or 0)
        if previous_failure_id:
            db.mark_failed_job_resolved(previous_failure_id)
        db.mark_failed_jobs_resolved_for_url(str(job.get("url", "")))
        completed = db.transition_owned_queue_job(
            job_id, self.owner_id, expected_statuses="finalizing",
            status="done", progress=100,
            progress_text=(
                "Verified upgrade version activated"
                if result.get("is_upgrade") else "Complete"
            ),
            completed_at=datetime.now(timezone.utc).isoformat(),
            history_id=int(history_id or 0), output_dir=output_dir,
            finalize_error="", failure_id=0,
        )
        if completed:
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
        if (
            not job
            or job.get("execution_owner") != self.owner_id
            or job.get("status") not in {
                "fetching", "downloading", "finalizing", "running", "cancelling",
            }
            or self._stopping
        ):
            if dispatch:
                self._dispatch()
            return
        output_dir = str(output_dir or job.get("output_dir", "") or self.output_dir)
        resume_sidecar = os.path.join(output_dir, ".streamkeep_resume.json")
        safe_error = sanitize_failure_reason(error or "Unknown error")
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
        failed = db.transition_owned_queue_job(
            job_id, self.owner_id,
            expected_statuses={
                "fetching", "downloading", "finalizing", "running", "cancelling",
            },
            status="failed", error=safe_error,
            failure_id=failure_id, failed_at=datetime.now(timezone.utc).isoformat(),
        )
        if failed:
            write_log_line(
                f"[SERVICE] Job {job_id} failed during {stage}: {safe_error}"
            )
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
        YtDlpExtractor.youtube_player_client = str(
            self.config.get("youtube_player_client", "") or ""
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
        YtDlpExtractor.sponsorblock_mark = str(
            self.config.get("sponsorblock_mark", "") or ""
        )
        YtDlpExtractor.sponsorblock_remove = str(
            self.config.get(
                "sponsorblock_remove", "sponsor,selfpromo,interaction"
            ) or ""
        )
        YtDlpExtractor.sponsorblock_api = str(
            self.config.get("sponsorblock_api", "") or ""
        )
        set_native_proxy(YtDlpExtractor.proxy)
