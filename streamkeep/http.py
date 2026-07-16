"""HTTP helpers built on top of `curl`.

All native extractors share these. Proxy routing honors a single
module-level `NATIVE_PROXY` — the Settings tab updates it via
`set_native_proxy()` so every new request picks up the change.
"""

from __future__ import annotations

import base64
from contextlib import contextmanager
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
import hashlib
import json
import logging
import os
import re
import subprocess
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

from .capabilities import CapabilityUnavailableError, resolve_tool_command
from .paths import _CREATE_NO_WINDOW
from .utils import fmt_size

# Module-level proxy URL — set by the Settings tab so all native extractor
# curl calls (Kick API, Twitch GraphQL, Rumble embed, etc.) honor the same
# proxy. Use `set_native_proxy()` / `get_native_proxy()` from other modules.
NATIVE_PROXY = ""
_INTERRUPT_STATE = threading.local()

# Host-scoped request profiles — per-host headers/referrer that are applied
# only to matching requests and never leak across hosts.
_HOST_PROFILES_LOCK = threading.Lock()
_HOST_PROFILES = {}  # host_lower -> {"headers": {str: str}, "referrer": str}


def set_host_profiles(profiles):
    """Replace all host profiles from a config dict.

    *profiles* maps hostname (lowercase) to dicts with optional keys
    ``headers`` (dict[str, str]) and ``referrer`` (str).
    Only ``http`` and ``https`` schemes are accepted for referrers.
    """
    cleaned = {}
    for host, profile in (profiles or {}).items():
        host = str(host).strip().lower()
        if not host:
            continue
        entry = {}
        hdrs = profile.get("headers") if isinstance(profile, dict) else None
        if isinstance(hdrs, dict):
            entry["headers"] = {str(k): str(v) for k, v in hdrs.items()}
        ref = str(profile.get("referrer", "") or "") if isinstance(profile, dict) else ""
        if ref and ref.startswith(("http://", "https://")):
            entry["referrer"] = ref
        if entry:
            cleaned[host] = entry
    with _HOST_PROFILES_LOCK:
        _HOST_PROFILES.clear()
        _HOST_PROFILES.update(cleaned)


def get_host_profiles():
    with _HOST_PROFILES_LOCK:
        return dict(_HOST_PROFILES)


def _host_profile_for_url(url):
    """Return the host profile dict for a URL, or None."""
    try:
        from urllib.parse import urlsplit
        host = urlsplit(url).hostname or ""
        host = host.lower()
    except Exception:
        return None
    with _HOST_PROFILES_LOCK:
        return _HOST_PROFILES.get(host)


@dataclass
class CommandResult:
    returncode: int = -1
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    interrupted: bool = False
    error: str = ""


def set_native_proxy(url: str) -> None:
    global NATIVE_PROXY
    NATIVE_PROXY = url or ""


def get_native_proxy() -> str:
    return NATIVE_PROXY


@contextmanager
def http_interruptible(checker):
    previous = getattr(_INTERRUPT_STATE, "checker", None)
    _INTERRUPT_STATE.checker = checker
    try:
        yield
    finally:
        if previous is None:
            try:
                delattr(_INTERRUPT_STATE, "checker")
            except AttributeError:
                pass
        else:
            _INTERRUPT_STATE.checker = previous


def http_interrupted():
    checker = getattr(_INTERRUPT_STATE, "checker", None)
    if checker is None:
        return False
    try:
        return bool(checker())
    except Exception as e:
        logger.debug("http_interrupted checker raised: %s", e)
        return False


def _terminate_process(proc):
    try:
        if proc.poll() is None:
            proc.terminate()
    except Exception:
        return
    try:
        proc.wait(timeout=1.5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def run_capture_interruptible(cmd: list[str], timeout: float = 30) -> CommandResult:
    timeout = max(float(timeout or 0), 0.1)
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_CREATE_NO_WINDOW,
        )
    except FileNotFoundError as e:
        logger.debug("Command not found: %s", e)
        return CommandResult(returncode=127, stderr=str(e), error=str(e))
    except Exception as e:
        logger.debug("Command failed: %s", e)
        return CommandResult(returncode=-1, stderr=str(e), error=str(e))

    deadline = time.monotonic() + timeout
    while True:
        if http_interrupted():
            _terminate_process(proc)
            return CommandResult(interrupted=True)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _terminate_process(proc)
            return CommandResult(timed_out=True)
        try:
            stdout, stderr = proc.communicate(timeout=min(0.2, max(remaining, 0.05)))
            return CommandResult(
                returncode=proc.returncode or 0,
                stdout=stdout or "",
                stderr=stderr or "",
            )
        except subprocess.TimeoutExpired:
            continue


def _build_curl_cmd(url: str, headers: dict[str, str] | None = None, method: str | None = None, body: str | None = None, timeout: float = 30) -> list[str]:
    """Assemble a curl command with proxy + cookies + headers + optional POST body.
    Shared by curl/curl_json/curl_post_json so the proxy/header logic
    lives in exactly one place."""
    max_time = max(1, int(timeout or 30))
    connect_timeout = max(1, min(10, max_time))
    cmd = [
        resolve_tool_command("curl"), "-s", "-L",
        "--proto", "=http,https",
        "--max-redirs", "5",
        "--connect-timeout", str(connect_timeout),
        "--max-time", str(max_time),
    ]
    _proxy = _resolve_proxy(url)
    if _proxy:
        cmd.extend(["-x", _proxy])
    _append_cookie_args(cmd)
    if method and method.upper() != "GET":
        cmd.extend(["-X", method.upper()])
    if body is not None:
        cmd.extend(["-H", "Content-Type: application/json", "-d", body])
    profile = _host_profile_for_url(url)
    if profile:
        for k, v in profile.get("headers", {}).items():
            cmd.extend(["-H", f"{k}: {v}"])
        ref = profile.get("referrer", "")
        if ref:
            cmd.extend(["-e", ref])
    for k, v in (headers or {}).items():
        cmd.extend(["-H", f"{k}: {v}"])
    cmd.append(url)
    return cmd


def _resolve_proxy(url):
    """Resolve the best proxy for a URL, honoring the pool then fallback."""
    from .proxy import resolve_proxy as _resolve_proxy_for_url

    return _resolve_proxy_for_url(url) or NATIVE_PROXY


def _append_cookie_args(cmd):
    """Add cookies.txt to a curl command if one is available."""
    from .cookies import cookies_file_path

    cpath = cookies_file_path()
    if cpath:
        cmd.extend(["--cookie", cpath])


def curl(url: str, headers: dict[str, str] | None = None, timeout: float = 30) -> str | None:
    """Run curl and return stdout or None."""
    try:
        cmd = _build_curl_cmd(url, headers, timeout=timeout)
    except CapabilityUnavailableError as error:
        logger.warning("Blocked curl request: %s", error)
        return None
    result = run_capture_interruptible(cmd, timeout=timeout + 2)
    return result.stdout if result.returncode == 0 else None


def curl_json(url: str, headers: dict[str, str] | None = None, timeout: float = 30) -> Any:
    """Run curl and parse JSON response."""
    body = curl(url, headers, timeout)
    if body:
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return None
    return None


def curl_post_json(url: str, data: Any, headers: dict[str, str] | None = None, timeout: float = 30) -> Any:
    """POST JSON and parse response."""
    try:
        cmd = _build_curl_cmd(
            url,
            headers,
            method="POST",
            body=json.dumps(data),
            timeout=timeout,
        )
        result = run_capture_interruptible(cmd, timeout=timeout + 2)
        if result.returncode == 0:
            return json.loads(result.stdout)
    except Exception as e:
        logger.debug("curl_post_json failed for %s: %s", url, e)
    return None


def _parse_final_response_headers(raw_headers):
    """Return ``(status, headers)`` for the final HTTP response block."""
    normalized = str(raw_headers or "").replace("\r\n", "\n")
    blocks = [block.strip() for block in re.split(r"\n\s*\n", normalized)
              if block.strip()]
    response_blocks = [block for block in blocks
                       if block.lstrip().upper().startswith("HTTP/")]
    final = response_blocks[-1] if response_blocks else ""
    lines = final.splitlines()
    status = 0
    if lines:
        match = re.match(r"HTTP/[^\s]+\s+(\d{3})\b", lines[0], re.IGNORECASE)
        if match:
            status = int(match.group(1))
    headers = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        headers[name.strip().lower()] = value.strip()
    return status, headers


def http_head_details(url, timeout=20):
    """HEAD request returning range, validator, and digest metadata."""
    connect_timeout = max(1, min(10, int(timeout or 20)))
    try:
        cmd = [
            resolve_tool_command("curl"), "-sI", "-L",
            "--proto", "=http,https",
            "--max-redirs", "5",
            "--connect-timeout", str(connect_timeout),
            "--max-time", str(max(1, int(timeout or 20))),
        ]
        _proxy = _resolve_proxy(url)
        if _proxy:
            cmd.extend(["-x", _proxy])
        _append_cookie_args(cmd)
        cmd.append(url)
        result = run_capture_interruptible(cmd, timeout=timeout + 2)
        if result.returncode != 0:
            return {}
        status, headers = _parse_final_response_headers(result.stdout)
        try:
            size = int(headers.get("content-length", "0"))
        except (TypeError, ValueError):
            size = 0
        return {
            "status": status,
            "content_length": size,
            "accepts_ranges": headers.get("accept-ranges", "").lower() == "bytes",
            "etag": headers.get("etag", ""),
            "last_modified": headers.get("last-modified", ""),
            "content_digest": headers.get("content-digest", ""),
            "repr_digest": headers.get("repr-digest", ""),
        }
    except Exception as e:
        logger.debug("http_head failed for %s: %s", url, e)
        return {}


def http_head(url, timeout=20):
    """HEAD request returning ``(status, content_length, accepts_ranges)``."""
    details = http_head_details(url, timeout=timeout)
    return (
        int(details.get("status", 0) or 0),
        int(details.get("content_length", 0) or 0),
        bool(details.get("accepts_ranges", False)),
    )


def _safe_if_range_validator(details):
    """Select a strong ETag or syntactically valid Last-Modified value."""
    etag = str(details.get("etag", "") or "").strip()
    if etag and etag != "*" and not etag.lower().startswith("w/"):
        return "etag", etag
    last_modified = str(details.get("last_modified", "") or "").strip()
    if last_modified:
        try:
            parsedate_to_datetime(last_modified)
            return "last-modified", last_modified
        except (TypeError, ValueError, OverflowError):
            pass
    return "", ""


def _parallel_resume_metadata(url, total, ranges, details):
    validator_kind, validator = _safe_if_range_validator(details)
    return {
        "version": 1,
        # Signed media URLs can contain short-lived credentials.  A digest is
        # enough to bind resume state without persisting those query values.
        "url_sha256": hashlib.sha256(str(url).encode("utf-8")).hexdigest(),
        "content_length": int(total),
        "validator_kind": validator_kind,
        "validator": validator,
        "content_digest": str(details.get("content_digest", "") or ""),
        "repr_digest": str(details.get("repr_digest", "") or ""),
        "ranges": [[int(start), int(end)] for _, start, end in ranges],
    }


def _load_parallel_resume_metadata(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _write_parallel_resume_metadata(path, data):
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        return True
    except OSError:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        return False


def _clear_parallel_resume_files(tmp_dir):
    try:
        entries = list(os.scandir(tmp_dir))
    except OSError:
        return
    for entry in entries:
        if not entry.is_file(follow_symlinks=False):
            continue
        if entry.name.startswith("part_") or entry.name in {
            "resume.json", "resume.json.tmp",
        }:
            try:
                os.remove(entry.path)
            except OSError:
                pass


def _parse_digest_header(value):
    supported = []
    for item in str(value or "").split(","):
        match = re.match(r"\s*(sha-256|sha-512)\s*=\s*:([^:]+):",
                         item, re.IGNORECASE)
        if not match:
            continue
        algorithm = match.group(1).lower()
        try:
            expected = base64.b64decode(match.group(2), validate=True)
        except (ValueError, TypeError):
            continue
        supported.append((algorithm, expected))
    return supported


def _verify_response_digests(path, details):
    digest_headers = [
        ("Content-Digest", details.get("content_digest", "")),
        ("Repr-Digest", details.get("repr_digest", "")),
    ]
    computed = {}
    for header_name, value in digest_headers:
        if not value:
            continue
        specs = _parse_digest_header(value)
        if not specs:
            return False, f"{header_name} has no supported valid digest"
        for algorithm, expected in specs:
            if algorithm not in computed:
                hasher = hashlib.new(algorithm.replace("-", ""))
                try:
                    with open(path, "rb") as handle:
                        while True:
                            chunk = handle.read(1024 * 1024)
                            if not chunk:
                                break
                            hasher.update(chunk)
                except OSError as exc:
                    return False, f"digest read failed: {exc}"
                computed[algorithm] = hasher.digest()
            if computed[algorithm] != expected:
                return False, f"{header_name} {algorithm} mismatch"
    return True, ""


def http_probe(url, headers=None, timeout=20):
    """Probe a URL and return response metadata.

    Returns ``{"status": int, "content_type": str, "final_url": str}``
    or an empty dict on failure.
    """
    cmd = _build_curl_cmd(url, headers=headers, timeout=timeout)
    cmd[1:1] = ["-I", "-w", r"\n%{content_type}\n%{url_effective}\n"]
    try:
        result = run_capture_interruptible(cmd, timeout=timeout + 2)
        if result.returncode != 0:
            return {}
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if len(lines) < 2:
            return {}
        content_type = lines[-2].split(";", 1)[0].strip().lower()
        final_url = lines[-1]
        status = 0
        for line in lines:
            if line.startswith("HTTP/"):
                parts = line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    status = int(parts[1])
        return {
            "status": status,
            "content_type": content_type,
            "final_url": final_url or url,
        }
    except Exception as e:
        logger.debug("http_probe failed for %s: %s", url, e)
        return {}


def parallel_http_download(url, outfile, connections=4, progress_cb=None,
                           cancel_check=None, min_size_mb=8, log_fn=None):
    """Download a direct HTTP file using N parallel Range requests.

    Probes with HEAD first. Returns False if the server lacks Range
    support, the file is too small to benefit from splitting, or any
    chunk fails — caller is expected to fall back to ffmpeg.
    """
    try:
        curl_path = resolve_tool_command("curl")
    except CapabilityUnavailableError as error:
        if log_fn:
            log_fn(f"[PARALLEL] blocked: {error}")
        return False
    head = http_head_details(url)
    status = int(head.get("status", 0) or 0)
    total = int(head.get("content_length", 0) or 0)
    accepts = bool(head.get("accepts_ranges", False))
    if status >= 400 or not accepts or total < min_size_mb * 1024 * 1024:
        if log_fn:
            log_fn(
                f"[PARALLEL] skip (status={status}, ranges={accepts}, "
                f"size={fmt_size(total) if total else '?'})"
            )
        return False

    connections = max(1, min(16, int(connections)))
    chunk = total // connections
    ranges = []
    for i in range(connections):
        start = i * chunk
        end = total - 1 if i == connections - 1 else (start + chunk - 1)
        ranges.append((i, start, end))

    tmp_dir = outfile + ".parts"
    try:
        os.makedirs(tmp_dir, exist_ok=True)
    except OSError as e:
        if log_fn:
            log_fn(f"[PARALLEL] temp dir failed: {e}")
        return False

    part_paths = [os.path.join(tmp_dir, f"part_{i:02d}") for i in range(connections)]
    header_paths = [part + ".headers" for part in part_paths]
    metadata_path = os.path.join(tmp_dir, "resume.json")
    resume_metadata = _parallel_resume_metadata(url, total, ranges, head)
    previous_metadata = _load_parallel_resume_metadata(metadata_path)
    resume_allowed = bool(
        resume_metadata["validator"]
        and previous_metadata == resume_metadata
    )
    if not resume_allowed:
        if previous_metadata and log_fn:
            log_fn("[PARALLEL] invalidating stale or unverifiable range parts")
        _clear_parallel_resume_files(tmp_dir)
    if not _write_parallel_resume_metadata(metadata_path, resume_metadata):
        if log_fn:
            log_fn("[PARALLEL] could not persist representation metadata")
        _clear_parallel_resume_files(tmp_dir)
        try:
            os.rmdir(tmp_dir)
        except OSError:
            pass
        return False

    bytes_done = [0] * connections
    errors = []
    procs = {}
    lock = threading.Lock()

    def worker(i, start, end):
        part = part_paths[i]
        header_path = header_paths[i]
        expected = end - start + 1
        if (resume_allowed and os.path.exists(part)
                and os.path.getsize(part) == expected):
            bytes_done[i] = expected
            return
        try:
            if os.path.exists(part):
                os.remove(part)
            if os.path.exists(header_path):
                os.remove(header_path)
        except OSError:
            pass
        cmd = [
            curl_path, "-sS", "-L", "--fail",
            "--proto", "=http,https",
            "--max-redirs", "5",
            "--retry", "2", "--retry-delay", "1",
            "-D", header_path,
            "-r", f"{start}-{end}",
            "-o", part,
        ]
        if resume_metadata["validator"]:
            cmd.extend(["-H", f"If-Range: {resume_metadata['validator']}"])
        if proxy_url:
            cmd.extend(["-x", proxy_url])
        _append_cookie_args(cmd)
        cmd.append(url)
        proc = None
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                creationflags=_CREATE_NO_WINDOW,
            )
            with lock:
                procs[i] = proc
            while proc.poll() is None:
                if cancel_check and cancel_check():
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    try:
                        proc.wait(timeout=2)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                    return
                if os.path.exists(part):
                    try:
                        bytes_done[i] = os.path.getsize(part)
                    except OSError:
                        pass
                time.sleep(0.25)
            if os.path.exists(part):
                bytes_done[i] = os.path.getsize(part)
            if proc.returncode != 0:
                err = ""
                try:
                    raw = proc.stderr.read() or b""
                    # stderr is bytes when text=True is not passed
                    err = (raw.decode("utf-8", errors="replace")
                           if isinstance(raw, bytes) else raw).strip()[:120]
                except Exception as e:
                    logger.debug("Failed to read stderr for part %d: %s", i, e)
                with lock:
                    errors.append(f"part {i}: curl exit {proc.returncode} {err}")
            else:
                try:
                    with open(header_path, "r", encoding="iso-8859-1") as handle:
                        response_status, response_headers = _parse_final_response_headers(
                            handle.read()
                        )
                except OSError as exc:
                    response_status, response_headers = 0, {}
                    with lock:
                        errors.append(f"part {i}: response headers unavailable: {exc}")
                if response_status:
                    content_range = response_headers.get("content-range", "")
                    match = re.fullmatch(
                        r"bytes\s+(\d+)-(\d+)/(\d+)",
                        content_range.strip(),
                        re.IGNORECASE,
                    )
                    valid_range = bool(
                        match
                        and int(match.group(1)) == start
                        and int(match.group(2)) == end
                        and int(match.group(3)) == total
                    )
                    if response_status != 206 or not valid_range:
                        with lock:
                            errors.append(
                                f"part {i}: expected 206 bytes {start}-{end}/{total}, "
                                f"got {response_status} {content_range or '<missing>'}"
                            )
        except FileNotFoundError:
            with lock:
                errors.append("curl not in PATH")
        except Exception as e:
            with lock:
                errors.append(f"part {i}: {e}")
        finally:
            if proc is not None and proc.stderr is not None:
                try:
                    proc.stderr.close()
                except Exception:
                    pass

    threads = []
    proxy_url = _resolve_proxy(url)
    for i, start, end in ranges:
        t = threading.Thread(target=worker, args=(i, start, end), daemon=True)
        t.start()
        threads.append(t)

    last_report = time.time()
    last_bytes = sum(bytes_done)
    while any(t.is_alive() for t in threads):
        if cancel_check and cancel_check():
            with lock:
                for p in procs.values():
                    try:
                        if p.poll() is None:
                            p.terminate()
                    except Exception:
                        pass
            break
        now = time.time()
        if progress_cb and now - last_report >= 0.4:
            total_done = sum(bytes_done)
            elapsed = max(now - last_report, 0.001)
            speed = max(0.0, (total_done - last_bytes) / elapsed)
            try:
                progress_cb(total_done, total, speed)
            except Exception:
                pass
            last_report = now
            last_bytes = total_done
        time.sleep(0.2)

    for t in threads:
        t.join(timeout=5)

    def _cleanup_parts():
        _clear_parallel_resume_files(tmp_dir)
        try:
            os.rmdir(tmp_dir)
        except OSError:
            pass

    if cancel_check and cancel_check():
        _cleanup_parts()
        return False

    if errors:
        if log_fn:
            log_fn(f"[PARALLEL] failed: {'; '.join(errors[:3])}")
        _cleanup_parts()
        return False

    for idx, (_, start, end) in enumerate(ranges):
        expected = end - start + 1
        actual = os.path.getsize(part_paths[idx]) if os.path.exists(part_paths[idx]) else 0
        if actual != expected:
            if log_fn:
                log_fn(
                    f"[PARALLEL] size mismatch on part {idx}: "
                    f"{actual} != {expected}"
                )
            _cleanup_parts()
            return False

    try:
        with open(outfile, "wb") as out:
            for p in part_paths:
                with open(p, "rb") as f:
                    while True:
                        buf = f.read(1024 * 1024)
                        if not buf:
                            break
                        out.write(buf)
    except Exception as e:
        if log_fn:
            log_fn(f"[PARALLEL] concat failed: {e}")
        # Remove the partially-written output file as well as the parts
        try:
            if os.path.exists(outfile):
                os.remove(outfile)
        except OSError:
            pass
        _cleanup_parts()
        return False

    digest_ok, digest_error = _verify_response_digests(outfile, head)
    if not digest_ok:
        if log_fn:
            log_fn(f"[PARALLEL] digest verification failed: {digest_error}")
        try:
            if os.path.exists(outfile):
                os.remove(outfile)
        except OSError:
            pass
        _cleanup_parts()
        return False

    _cleanup_parts()

    if progress_cb:
        try:
            progress_cb(total, total, 0)
        except Exception:
            pass
    return True
