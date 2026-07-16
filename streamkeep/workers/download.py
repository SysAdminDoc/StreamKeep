"""Download worker — ffmpeg-based segmented downloader with parallel
HTTP Range fallback and yt-dlp direct download mode."""

import logging
import os
import re
import subprocess
import time
from glob import glob
from pathlib import Path

logger = logging.getLogger(__name__)

from PyQt6.QtCore import QThread, pyqtSignal

from ..capabilities import (
    CapabilityUnavailableError,
    require_capability,
    resolve_tool_command,
)
from ..http import parallel_http_download
from ..paths import FFMPEG_SAFETY, _CREATE_NO_WINDOW
from ..postprocess.codecs import AUDIO_EXTS, VIDEO_EXTS
from ..resume import (
    clear_resume_state, merge_completed, save_resume_state,
)
from ..utils import fmt_duration, fmt_size


class DownloadWorker(QThread):
    """Downloads segments using ffmpeg with speed/ETA tracking."""

    progress = pyqtSignal(int, int, str)   # seg_idx, percent, status
    segment_done = pyqtSignal(int, str)
    log = pyqtSignal(str)
    error = pyqtSignal(int, str)
    all_done = pyqtSignal()

    def __init__(self, playlist_url, segments, output_dir, format_type="hls"):
        super().__init__()
        self.playlist_url = playlist_url
        self.segments = segments
        self.output_dir = output_dir
        self.format_type = format_type
        self.audio_url = ""
        self.ytdlp_source = ""
        self.ytdlp_format = ""
        self.ytdlp_format_sort = ""
        self.ytdlp_container = "mp4"
        self.ytdlp_audio_format = ""
        self.ytdlp_audio_quality = ""
        self.cookies_browser = ""
        self.rate_limit = ""
        self.proxy = ""
        self.download_subs = False
        self.subtitle_languages = "en.*,en"
        self.subtitle_auto = True
        self.subtitle_convert = ""
        self.subtitle_embed = True
        self.sponsorblock = False
        self.download_sections = ""  # yt-dlp --download-sections value (F21)
        self.max_retries = 2
        self.parallel_connections = 4
        # When > 0 and the segment is a live capture (duration <= 0), the
        # ffmpeg command switches to the `segment` muxer so a long live
        # recording is written as `<label>_part%03d.mp4` chunks instead of
        # one monolithic 40+ GB file.
        self.chunk_length_secs = 0
        self._cancel = False
        self._proc = None
        self._ffmpeg_path = ""
        self._proc_lock = __import__("threading").Lock()
        # Resume sidecar state. When set the worker keeps it fresh on
        # segment completion and clears it on a clean finish. Callers
        # (main_window) attach this via `attach_resume_state` just before
        # `start()` so the worker doesn't need to know about extractor
        # metadata.
        self._resume_state = None

    def attach_resume_state(self, state):
        """Attach a ResumeState. The worker will write it on start, refresh
        it on each segment_done, and clear it on clean all_done.

        `state.completed` is treated as authoritative on start — any segment
        already listed is considered complete and skipped (after verifying
        the file still exists on disk).
        """
        self._resume_state = state
        if state is not None:
            # Pull shape from the worker so the sidecar is self-contained.
            state.playlist_url = self.playlist_url
            state.format_type = self.format_type
            state.audio_url = self.audio_url or ""
            state.ytdlp_source = self.ytdlp_source or ""
            state.ytdlp_format = self.ytdlp_format or ""
            state.ytdlp_format_sort = self.ytdlp_format_sort or ""
            state.ytdlp_container = self.ytdlp_container or "mp4"
            state.ytdlp_audio_format = self.ytdlp_audio_format or ""
            state.ytdlp_audio_quality = self.ytdlp_audio_quality or ""
            state.download_subs = bool(self.download_subs)
            state.subtitle_languages = self.subtitle_languages or ""
            state.subtitle_auto = bool(self.subtitle_auto)
            state.subtitle_convert = self.subtitle_convert or ""
            state.subtitle_embed = bool(self.subtitle_embed)
            state.output_dir = self.output_dir
            state.segments = [list(s) for s in self.segments]
            if self.format_type == "ytdlp_direct" and self.segments:
                label = self.segments[0][1]
                _template, expected = self._ytdlp_output_paths(label)
                state.expected_outfile = expected or os.path.join(
                    self.output_dir, f"{label}.%(ext)s"
                )
            save_resume_state(state)

    def cancel(self):
        self._cancel = True
        with self._proc_lock:
            if self._proc and self._proc.poll() is None:
                try:
                    self._proc.terminate()
                except OSError:
                    pass

    def _build_ytdlp_download_cmd(self, outfile, impersonate=False):
        """Assemble the yt-dlp download command for a single segment."""
        from ..download_options import (
            validate_download_options, validate_subtitle_options,
        )
        from ..extractors.ytdlp import ytdlp_command, ytdlp_impersonate_args

        options = validate_download_options(
            format_spec=self.ytdlp_format,
            format_sort=self.ytdlp_format_sort,
            container=self.ytdlp_container,
            audio_format=self.ytdlp_audio_format,
            audio_quality=self.ytdlp_audio_quality,
        )
        subtitle_options = validate_subtitle_options(
            enabled=self.download_subs,
            languages=self.subtitle_languages,
            automatic=self.subtitle_auto,
            convert=self.subtitle_convert,
            embed=self.subtitle_embed,
        )
        format_spec = options["format_spec"]
        if options["audio_format"] and not self.ytdlp_format:
            format_spec = "bestaudio/best"
        if not format_spec:
            raise ValueError("yt-dlp format specification is empty")

        ffmpeg_path = self._ffmpeg_path or resolve_tool_command("ffmpeg")
        cmd = ytdlp_command() + [
            "-f", format_spec,
            "--no-part",
            "--newline",
            "--progress",
            "-o", outfile,
            "--no-playlist",
            "--ffmpeg-location", str(Path(ffmpeg_path).parent),
        ]
        if options["format_sort"]:
            cmd.extend(["-S", options["format_sort"]])
        if options["audio_format"]:
            cmd.extend(["-x", "--audio-format", options["audio_format"]])
            if options["audio_quality"]:
                cmd.extend(["--audio-quality", options["audio_quality"]])
        elif options["container"] != "original":
            # --merge-output-format controls separate video/audio merges;
            # --remux-video also gives single-file formats the requested
            # container without re-encoding.
            cmd.extend([
                "--merge-output-format", options["container"],
                "--remux-video", options["container"],
            ])
        if self.cookies_browser:
            cmd.extend(["--cookies-from-browser", self.cookies_browser])
        # Inject cookies.txt for authenticated downloads (F47)
        if not self.cookies_browser:
            from ..cookies import cookies_file_path
            cpath = cookies_file_path()
            if cpath:
                cmd.extend(["--cookies", cpath])
        if self.rate_limit:
            cmd.extend(["--limit-rate", self.rate_limit])
        if self.proxy:
            cmd.extend(["--proxy", self.proxy])
        if subtitle_options["enabled"]:
            cmd.extend([
                "--write-subs",
                "--sub-langs", subtitle_options["languages"],
            ])
            cmd.append(
                "--write-auto-subs" if subtitle_options["automatic"]
                else "--no-write-auto-subs"
            )
            if subtitle_options["convert"]:
                cmd.extend(["--convert-subs", subtitle_options["convert"]])
            can_embed = subtitle_options["embed"] and not options["audio_format"]
            cmd.append("--embed-subs" if can_embed else "--no-embed-subs")
        if self.sponsorblock:
            cmd.extend(["--sponsorblock-remove", "sponsor,selfpromo,interaction"])
        if self.download_sections:
            cmd.extend(["--download-sections", self.download_sections])
        if impersonate:
            cmd.extend(ytdlp_impersonate_args())
        try:
            from ..extractors.ytdlp import (
                _is_youtube_url,
                format_ytdlp_runtime_warning,
                ytdlp_runtime_args,
                ytdlp_runtime_status,
            )
            if _is_youtube_url(self.ytdlp_source):
                runtime_status = ytdlp_runtime_status()
                warning = format_ytdlp_runtime_warning(runtime_status)
                if warning:
                    self.log.emit(f"[WARN] {warning}")
                cmd.extend(ytdlp_runtime_args(runtime_status))
        except Exception as e:
            self.log.emit(f"[WARN] Could not check yt-dlp runtime support: {e}")
        cmd.append(self.ytdlp_source)
        return cmd

    def _download_with_ytdlp(
        self, seg_idx, label, outfile, expected_outfile=None
    ):
        """Download via yt-dlp directly. Handles DASH merge, format
        selection, and a one-shot Cloudflare-impersonation retry. Returns
        True on success."""
        from ..extractors.ytdlp import (
            _impersonation_available,
            _looks_like_cloudflare,
        )

        attempted_impersonate = False
        while True:
            try:
                cmd = self._build_ytdlp_download_cmd(
                    outfile, impersonate=attempted_impersonate
                )
            except (TypeError, ValueError) as error:
                self.log.emit(f"[ERROR] Invalid yt-dlp output options: {error}")
                return False
            outcome, output_lines = self._stream_ytdlp_download(
                cmd, seg_idx, label, outfile, expected_outfile
            )
            if outcome == "ok":
                return True
            if outcome == "cancel":
                return False
            # outcome == "fail": retry once behind Cloudflare with a real
            # browser TLS fingerprint before surfacing the failure.
            if (not attempted_impersonate and _impersonation_available()
                    and _looks_like_cloudflare("\n".join(output_lines))):
                attempted_impersonate = True
                self.log.emit(
                    "[RETRY] Site looks Cloudflare-protected - retrying "
                    "download with browser impersonation..."
                )
                continue
            err_tail = "\n".join(output_lines[-5:])
            self.log.emit(f"[FAIL] {label}\n{err_tail}")
            return False

    def _stream_ytdlp_download(
        self, cmd, seg_idx, label, outfile, expected_outfile=None
    ):
        """Run one yt-dlp download attempt, streaming progress.

        Returns ``(outcome, output_lines)`` where *outcome* is ``"ok"`` on a
        verified file, ``"cancel"`` when the user aborted, or ``"fail"`` for a
        retryable failure. The caller owns the final ``[FAIL]`` log so it can
        decide whether to retry first.
        """
        try:
            with self._proc_lock:
                self._proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1, encoding="utf-8", errors="replace",
                    creationflags=_CREATE_NO_WINDOW,
                )
            output_lines = []
            try:
                for line in self._proc.stdout:
                    line = line.strip()
                    if not line:
                        continue
                    output_lines.append(line)
                    pct_match = re.search(r'(\d+\.\d+)%\s+of\s+(~?[\d.]+\w+)', line)
                    if pct_match:
                        pct = int(float(pct_match.group(1)))
                        size_str = pct_match.group(2)
                        speed_match = re.search(r'at\s+([\d.]+\w+/s)', line)
                        eta_match = re.search(r'ETA\s+([\d:]+)', line)
                        extra = ""
                        if speed_match:
                            extra += f" | {speed_match.group(1)}"
                        if eta_match:
                            extra += f" | ETA {eta_match.group(1)}"
                        self.progress.emit(seg_idx, min(99, pct), f"{size_str}{extra}")
                    elif "[Merger]" in line or "Merging" in line:
                        self.progress.emit(seg_idx, 99, "Merging video+audio")
            finally:
                if self._proc.stdout:
                    self._proc.stdout.close()

            self._proc.wait()
            if self._cancel:
                self._remove_ytdlp_outputs(outfile, expected_outfile)
                return "cancel", output_lines

            # Legacy callers pass an exact output path. New downloads use an
            # extension template and discover the container yt-dlp produced.
            legacy_exact_output = (
                expected_outfile is None and "%(ext)s" not in outfile
            )
            if legacy_exact_output:
                expected_outfile = outfile
            if (self._proc.returncode == 0 and legacy_exact_output
                    and expected_outfile
                    and not os.path.exists(expected_outfile)):
                self._reconcile_output(expected_outfile)
            if (self._proc.returncode == 0 and expected_outfile
                    and not legacy_exact_output
                    and not os.path.exists(expected_outfile)):
                self.log.emit(
                    f"[ERROR] yt-dlp did not produce the requested "
                    f".{Path(expected_outfile).suffix.lstrip('.')} output"
                )
                self._remove_empty_ytdlp_outputs(outfile, expected_outfile)
                return "fail", output_lines
            produced = self._find_ytdlp_output(outfile, expected_outfile)

            if self._proc.returncode == 0 and produced:
                size = os.path.getsize(produced)
                self.progress.emit(seg_idx, 100, "Complete")
                self.segment_done.emit(seg_idx, fmt_size(size))
                self._mark_segment_done(seg_idx)
                self.log.emit(
                    f"[DONE] {label} - {fmt_size(size)} "
                    f"({Path(produced).suffix.lower().lstrip('.')})"
                )
                return "ok", output_lines

            self._remove_empty_ytdlp_outputs(outfile, expected_outfile)
            return "fail", output_lines

        except FileNotFoundError:
            self.log.emit(f"[ERROR] {label}: bundled yt-dlp could not be started")
            return "fail", []
        except Exception as e:
            self.log.emit(f"[ERROR] {label}: {e}")
            return "fail", []

    def _reconcile_output(self, outfile):
        """Rename a merged file yt-dlp wrote under a different container.

        yt-dlp's Merger names the output after the real container when it
        cannot honour the requested extension (e.g. ``<label>.mkv`` or
        ``<label>.mp4.mkv``). When the expected ``outfile`` is absent, adopt
        the largest sibling that shares the base name so the rest of the
        pipeline (resume sidecar, integrity manifest) sees the ``.mp4`` path
        it expects.
        """
        base, _ext = os.path.splitext(outfile)
        candidates = []
        for pattern in (base + ".*", outfile + ".*"):
            for path in glob(pattern):
                if os.path.abspath(path) == os.path.abspath(outfile):
                    continue
                try:
                    if (os.path.isfile(path) and os.path.getsize(path) > 0
                            and Path(path).suffix.lower() in (VIDEO_EXTS | AUDIO_EXTS)):
                        candidates.append(path)
                except OSError:
                    continue
        if not candidates:
            return
        produced = max(candidates, key=lambda p: os.path.getsize(p))
        try:
            os.replace(produced, outfile)
            self.log.emit(
                f"[INFO] Adopted merged output {os.path.basename(produced)} "
                f"-> {os.path.basename(outfile)}"
            )
        except OSError as e:
            self.log.emit(f"[WARN] Could not reconcile merged output: {e}")

    @staticmethod
    def _ytdlp_template_base(outfile):
        marker = ".%(ext)s"
        if outfile.endswith(marker):
            return outfile[:-len(marker)]
        return os.path.splitext(outfile)[0]

    def _find_ytdlp_output(self, outfile, expected_outfile=None):
        """Return the largest non-empty media file produced for a template."""
        if expected_outfile:
            try:
                if (os.path.isfile(expected_outfile)
                        and os.path.getsize(expected_outfile) > 0):
                    return expected_outfile
            except OSError:
                pass
        if "%(ext)s" not in outfile:
            try:
                if os.path.isfile(outfile) and os.path.getsize(outfile) > 0:
                    return outfile
            except OSError:
                pass
        base = self._ytdlp_template_base(outfile)
        candidates = []
        for path in glob(base + ".*"):
            try:
                if (os.path.isfile(path) and os.path.getsize(path) > 0
                        and Path(path).suffix.lower() in (VIDEO_EXTS | AUDIO_EXTS)):
                    candidates.append(path)
            except OSError:
                continue
        return max(candidates, key=os.path.getsize) if candidates else ""

    def _remove_empty_ytdlp_outputs(self, outfile, expected_outfile=None):
        base = self._ytdlp_template_base(outfile)
        paths = set(glob(base + ".*"))
        if expected_outfile:
            paths.add(expected_outfile)
        for path in paths:
            try:
                if os.path.isfile(path) and os.path.getsize(path) == 0:
                    os.remove(path)
            except OSError:
                pass

    def _remove_ytdlp_outputs(self, outfile, expected_outfile=None):
        base = self._ytdlp_template_base(outfile)
        paths = set(glob(base + ".*"))
        if expected_outfile:
            paths.add(expected_outfile)
        for path in paths:
            try:
                suffix = Path(path).suffix.lower()
                if os.path.isfile(path) and (
                    suffix in (VIDEO_EXTS | AUDIO_EXTS) or suffix == ".part"
                ):
                    os.remove(path)
            except OSError:
                pass

    def _ytdlp_output_paths(self, label):
        """Return ``(template, deterministic output path or '')``."""
        base = os.path.join(self.output_dir, label)
        template = base + ".%(ext)s"
        audio_format = str(self.ytdlp_audio_format or "").lower()
        if audio_format and audio_format != "best":
            return template, base + "." + audio_format
        container = str(self.ytdlp_container or "mp4").lower()
        if not audio_format and container != "original":
            return template, base + "." + container
        return template, ""

    def _mark_segment_done(self, seg_idx):
        """Merge the segment into the resume sidecar and persist it."""
        if self._resume_state is None:
            return
        merge_completed(self._resume_state, seg_idx)
        save_resume_state(self._resume_state)

    def _ensure_supported_ffmpeg(self):
        if self._ffmpeg_path:
            return True
        try:
            self._ffmpeg_path = resolve_tool_command("ffmpeg")
            return True
        except CapabilityUnavailableError as error:
            self.log.emit(f"[BLOCKED] {error}")
            return False

    def _ensure_supported_ytdlp(self):
        try:
            require_capability("yt_dlp")
            return True
        except CapabilityUnavailableError as error:
            self.log.emit(f"[BLOCKED] {error}")
            return False

    def run(self):
        logger.info("Download started: %d segment(s) to %s", len(self.segments), self.output_dir)
        all_succeeded = True
        for seg_idx, label, start, duration in self.segments:
            if self._cancel:
                self.log.emit("Download cancelled.")
                # Leave the sidecar in place — the user may resume.
                return

            is_ytdlp = (
                self.format_type == "ytdlp_direct"
                and self.ytdlp_source
                and (self.ytdlp_format or self.ytdlp_audio_format)
            )
            expected_ytdlp_outfile = ""
            if is_ytdlp:
                outfile, expected_ytdlp_outfile = self._ytdlp_output_paths(label)
                if expected_ytdlp_outfile:
                    existing_output = (
                        expected_ytdlp_outfile
                        if os.path.exists(expected_ytdlp_outfile) else ""
                    )
                else:
                    existing_output = self._find_ytdlp_output(outfile)
            else:
                outfile = os.path.join(self.output_dir, f"{label}.mp4")
                existing_output = outfile if os.path.exists(outfile) else ""
            is_live_capture = duration <= 0
            if existing_output:
                size = os.path.getsize(existing_output)
                # Use 64 KB minimum threshold — 1 KB was too small and would
                # treat corrupt/truncated files as complete.
                if size > 65536:
                    self.log.emit(f"[SKIP] {label} ({fmt_size(size)})")
                    self.segment_done.emit(seg_idx, fmt_size(size))
                    self._mark_segment_done(seg_idx)
                    continue
                try:
                    os.remove(existing_output)
                except OSError:
                    pass

            duration_label = "live" if is_live_capture else f"{duration}s"
            self.log.emit(f"[DL] {label} - start: {start}s, duration: {duration_label}")
            self.progress.emit(seg_idx, 0, "Starting...")

            # yt-dlp direct download mode
            if is_ytdlp:
                if not self._ensure_supported_ffmpeg():
                    self.error.emit(
                        seg_idx,
                        "Unsafe or missing FFmpeg; see log for repair guidance",
                    )
                    return
                if not self._ensure_supported_ytdlp():
                    self.error.emit(
                        seg_idx,
                        "Unsafe or missing yt-dlp; see log for repair guidance",
                    )
                    return
                success = self._download_with_ytdlp(
                    seg_idx, label, outfile, expected_ytdlp_outfile
                )
                if self._cancel:
                    return
                if not success:
                    all_succeeded = False
                    logger.error("yt-dlp download failed for segment %d (%s)", seg_idx, label)
                    self.error.emit(seg_idx, "yt-dlp download failed")
                continue

            # Multi-connection parallel download for direct MP4 URLs
            if (self.format_type == "mp4" and not self.audio_url
                    and self.parallel_connections > 1):
                worker_ref = self

                def _cb(done, total, speed):
                    pct = min(99, int((done / total) * 100)) if total > 0 else 0
                    spd = f"{fmt_size(speed)}/s" if speed > 0 else ""
                    eta = ""
                    if speed > 0 and total > done:
                        try:
                            eta = f" | ETA {fmt_duration((total - done) / speed)}"
                        except (ValueError, ZeroDivisionError, OverflowError):
                            eta = ""
                    status = f"{fmt_size(done)} / {fmt_size(total)} | {spd}{eta}"
                    worker_ref.progress.emit(seg_idx, pct, status)

                def _cancel():
                    return worker_ref._cancel

                try:
                    parallel_ok = parallel_http_download(
                        self.playlist_url, outfile,
                        connections=self.parallel_connections,
                        progress_cb=_cb, cancel_check=_cancel,
                        log_fn=lambda m: self.log.emit(m),
                    )
                except Exception as e:
                    self.log.emit(f"[PARALLEL ERR] {label}: {e}")
                    parallel_ok = False

                if self._cancel:
                    if os.path.exists(outfile):
                        try:
                            os.remove(outfile)
                        except OSError:
                            pass
                    return

                if (parallel_ok and os.path.exists(outfile)
                        and os.path.getsize(outfile) > 0):
                    size = os.path.getsize(outfile)
                    self.progress.emit(seg_idx, 100, "Complete")
                    self.segment_done.emit(seg_idx, fmt_size(size))
                    self._mark_segment_done(seg_idx)
                    self.log.emit(
                        f"[DONE] {label} - {fmt_size(size)} "
                        f"(parallel x{self.parallel_connections})"
                    )
                    continue
                else:
                    self.log.emit(f"[INFO] {label}: falling back to ffmpeg")

            if not self._ensure_supported_ffmpeg():
                self.error.emit(
                    seg_idx,
                    "Unsafe or missing FFmpeg; see log for repair guidance",
                )
                return

            # Live-only: split long captures into chunks via ffmpeg segment
            # muxer. The outfile pattern `<base>_part%03d.mp4` gives us
            # `live_recording_part000.mp4`, `_part001.mp4`, ... aligned
            # roughly to `chunk_length_secs`. Only applies to live captures
            # (duration <= 0) with no extra audio merge and no mp4 direct
            # branch — otherwise fall through to the normal cmd build.
            chunk_mode = (
                is_live_capture
                and self.chunk_length_secs > 0
                and not self.audio_url
                and self.format_type != "ytdlp_direct"
            )
            if chunk_mode:
                base = os.path.join(self.output_dir, f"{label}_part%03d.mp4")
                cmd = [
                    self._ffmpeg_path, *FFMPEG_SAFETY, "-hide_banner", "-loglevel", "info",
                    "-i", self.playlist_url,
                    "-c", "copy",
                    "-f", "segment",
                    "-segment_time", str(int(self.chunk_length_secs)),
                    "-reset_timestamps", "1",
                    "-strftime", "0",
                    "-y", base,
                ]
                self.log.emit(
                    f"[CHUNK] Live capture will be split every "
                    f"{self.chunk_length_secs}s into {label}_partNNN.mp4"
                )
                # Live chunked capture — single attempt (no retry on lives),
                # then fall through to the outer for-loop's next segment
                # (lives always have just one segment, so we'll exit).
                try:
                    with self._proc_lock:
                        self._proc = subprocess.Popen(
                            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            text=True, bufsize=1, encoding="utf-8",
                            errors="replace",
                            creationflags=_CREATE_NO_WINDOW,
                        )
                    for line in self._proc.stderr:
                        line = line.strip()
                        if not line:
                            continue
                        time_match = re.search(r'time=(\d+):(\d+):(\d+)\.(\d+)', line)
                        if time_match:
                            try:
                                h = int(time_match.group(1))
                                m = int(time_match.group(2))
                                s = int(time_match.group(3))
                            except (ValueError, IndexError):
                                continue
                            elapsed = h * 3600 + m * 60 + s
                            self.progress.emit(
                                seg_idx, 0,
                                f"{elapsed}s captured (chunked)",
                            )
                    self._proc.wait()
                    # On cancel / normal exit, count how many chunks got
                    # written so the UI can emit `segment_done` with a
                    # meaningful size.
                    total_size = 0
                    chunk_count = 0
                    try:
                        prefix = f"{label}_part"
                        for entry in os.scandir(self.output_dir):
                            if entry.is_file() and entry.name.startswith(prefix):
                                total_size += entry.stat().st_size
                                chunk_count += 1
                    except OSError:
                        pass
                    if chunk_count > 0:
                        self.segment_done.emit(seg_idx, fmt_size(total_size))
                        self.log.emit(
                            f"[DONE] {label} - {chunk_count} chunk(s), "
                            f"{fmt_size(total_size)} total"
                        )
                        self._mark_segment_done(seg_idx)
                    elif self._cancel:
                        pass
                    else:
                        all_succeeded = False
                        logger.error("Chunked capture produced no files for segment %d (%s)", seg_idx, label)
                        self.error.emit(seg_idx, "Chunked capture produced no files")
                except FileNotFoundError:
                    self.error.emit(seg_idx, "ffmpeg not found in PATH")
                    self.log.emit(f"[ERROR] {label}: ffmpeg not in PATH")
                    return
                except Exception as e:
                    self.log.emit(f"[ERROR] {label}: {e}")
                if self._cancel:
                    return
                continue  # next segment (but live is always single-seg)

            # Build ffmpeg command with optional audio merge
            if self.audio_url:
                if self.format_type == "mp4":
                    cmd = [
                        self._ffmpeg_path, *FFMPEG_SAFETY, "-hide_banner", "-loglevel", "info",
                        "-i", self.playlist_url, "-i", self.audio_url,
                        "-map", "0:v:0", "-map", "1:a:0",
                        "-c", "copy", "-y", outfile,
                    ]
                else:
                    cmd = [
                        self._ffmpeg_path, *FFMPEG_SAFETY, "-hide_banner", "-loglevel", "info",
                        "-ss", str(start), "-i", self.playlist_url,
                        "-ss", str(start), "-i", self.audio_url,
                        "-map", "0:v:0", "-map", "1:a:0",
                        "-c", "copy",
                    ]
                    if not is_live_capture:
                        cmd.extend(["-t", str(duration)])
                    cmd.extend(["-y", outfile])
            elif self.format_type == "mp4":
                cmd = [
                    self._ffmpeg_path, *FFMPEG_SAFETY, "-hide_banner", "-loglevel", "info",
                    "-i", self.playlist_url, "-c", "copy", "-y", outfile,
                ]
            else:
                cmd = [
                    self._ffmpeg_path, *FFMPEG_SAFETY, "-hide_banner", "-loglevel", "info",
                    "-ss", str(start), "-i", self.playlist_url, "-c", "copy",
                ]
                if not is_live_capture:
                    cmd.extend(["-t", str(duration)])
                cmd.extend(["-y", outfile])

            # Retry loop for transient ffmpeg failures
            attempts = self.max_retries + 1
            last_error = ""
            segment_done_flag = False
            for attempt in range(attempts):
                if self._cancel:
                    return
                if attempt > 0:
                    backoff = min(2 ** attempt, 60)  # cap at 60s
                    self.log.emit(
                        f"[RETRY {attempt}/{self.max_retries}] {label} "
                        f"(waiting {backoff}s)"
                    )
                    for _ in range(backoff * 2):
                        if self._cancel:
                            return
                        time.sleep(0.5)
                try:
                    # stdout is discarded — ffmpeg writes all progress to
                    # stderr. If stdout were piped without being drained,
                    # a ffmpeg mode that emits data to stdout (e.g. -f null
                    # mux reports) would deadlock on the pipe buffer.
                    with self._proc_lock:
                        self._proc = subprocess.Popen(
                            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            text=True, bufsize=1, encoding="utf-8",
                            errors="replace",
                            creationflags=_CREATE_NO_WINDOW,
                        )
                    output_lines = []
                    try:
                        for line in self._proc.stderr:
                            line = line.strip()
                            if not line:
                                continue
                            output_lines.append(line)
                            time_match = re.search(r'time=(\d+):(\d+):(\d+)\.(\d+)', line)
                            if time_match:
                                try:
                                    h = int(time_match.group(1))
                                    m = int(time_match.group(2))
                                    s = int(time_match.group(3))
                                except (ValueError, IndexError):
                                    continue
                                elapsed = h * 3600 + m * 60 + s
                                pct = (
                                    0 if is_live_capture
                                    else min(99, int((elapsed / max(duration, 1)) * 100))
                                )
                                speed_m = re.search(r'speed=\s*([\d.]+)x', line)
                                size_m = re.search(r'size=\s*(\d+\w+)', line)
                                extra = ""
                                if speed_m:
                                    spd = float(speed_m.group(1))
                                    extra += f" | {spd:.1f}x"
                                    if spd > 0 and duration > 0:
                                        remaining = (duration - elapsed) / spd
                                        extra += f" | ETA {fmt_duration(remaining)}"
                                if size_m:
                                    extra += f" | {size_m.group(1)}"
                                status = (
                                    f"{elapsed}s captured{extra}" if is_live_capture
                                    else f"{elapsed}s / {duration}s{extra}"
                                )
                                self.progress.emit(seg_idx, pct, status)
                    finally:
                        if self._proc.stderr:
                            self._proc.stderr.close()

                    self._proc.wait()
                    if self._cancel:
                        if is_live_capture and os.path.exists(outfile):
                            size = os.path.getsize(outfile)
                            if size > 1024:
                                self.segment_done.emit(seg_idx, fmt_size(size))
                                self.log.emit(
                                    f"[STOP] Kept partial live capture {label} - "
                                    f"{fmt_size(size)}"
                                )
                        elif os.path.exists(outfile):
                            try:
                                os.remove(outfile)
                            except OSError:
                                pass
                        return

                    if (self._proc.returncode == 0 and os.path.exists(outfile)
                            and os.path.getsize(outfile) > 0):
                        size = os.path.getsize(outfile)
                        self.progress.emit(seg_idx, 100, "Complete")
                        self.segment_done.emit(seg_idx, fmt_size(size))
                        self._mark_segment_done(seg_idx)
                        self.log.emit(f"[DONE] {label} - {fmt_size(size)}")
                        segment_done_flag = True
                        break
                    else:
                        if os.path.exists(outfile) and os.path.getsize(outfile) == 0:
                            try:
                                os.remove(outfile)
                            except OSError:
                                pass
                        last_error = "\n".join(output_lines[-5:]) or f"ffmpeg exit {self._proc.returncode}"
                        self.log.emit(f"[FAIL attempt {attempt + 1}/{attempts}] {label}")
                        if is_live_capture:
                            break

                except FileNotFoundError:
                    logger.error("ffmpeg not found in PATH for segment %d (%s)", seg_idx, label)
                    self.error.emit(seg_idx, "ffmpeg not found in PATH")
                    self.log.emit(f"[ERROR] {label}: ffmpeg not in PATH")
                    return
                except PermissionError as e:
                    logger.error("Permission denied for segment %d (%s): %s", seg_idx, label, e)
                    self.error.emit(seg_idx, f"Permission denied: {e}")
                    self.log.emit(f"[ERROR] {label}: {e}")
                    break
                except Exception as e:
                    logger.error("Unexpected error for segment %d (%s): %s", seg_idx, label, e)
                    last_error = str(e)
                    self.log.emit(f"[ERROR attempt {attempt + 1}/{attempts}] {label}: {e}")

            if not segment_done_flag and not self._cancel:
                all_succeeded = False
                if not is_live_capture:
                    logger.error("All retries exhausted for segment %d (%s): %s", seg_idx, label, last_error[:120])
                    self.error.emit(
                        seg_idx,
                        f"Failed after {attempts} attempt(s): {last_error[:120]}",
                    )
                    self.log.emit(f"[GIVE UP] {label} — all retries exhausted")

        if not self._cancel:
            # Only clear the resume sidecar and signal completion if every
            # segment actually succeeded.  When some segments failed, leave
            # the sidecar in place so the user can resume later.
            if self._resume_state is not None:
                expected = {s[0] for s in self.segments}
                completed = set(self._resume_state.completed)
                if expected != completed:
                    all_succeeded = False
            if all_succeeded:
                if self._resume_state is not None:
                    clear_resume_state(self.output_dir)
                self.all_done.emit()
            else:
                self.log.emit(
                    "[PARTIAL] Some segments failed — resume sidecar kept "
                    "for retry."
                )
