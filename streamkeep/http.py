"""HTTP helpers built on top of `curl`.

All native extractors share these. Proxy routing honors a single
module-level `NATIVE_PROXY` — the Settings tab updates it via
`set_native_proxy()` so every new request picks up the change.
"""

import json
import os
import re
import subprocess
import threading
import time

from .paths import _CREATE_NO_WINDOW
from .utils import fmt_size

# Module-level proxy URL — set by the Settings tab so all native extractor
# curl calls (Kick API, Twitch GraphQL, Rumble embed, etc.) honor the same
# proxy. Use `set_native_proxy()` / `get_native_proxy()` from other modules.
NATIVE_PROXY = ""


def set_native_proxy(url):
    global NATIVE_PROXY
    NATIVE_PROXY = url or ""


def get_native_proxy():
    return NATIVE_PROXY


def _build_curl_cmd(url, headers=None, method=None, body=None):
    """Assemble a curl command with proxy + headers + optional POST body.
    Shared by curl/curl_json/curl_post_json so the proxy/header logic
    lives in exactly one place."""
    cmd = ["curl", "-s", "-L"]
    if NATIVE_PROXY:
        cmd.extend(["-x", NATIVE_PROXY])
    if method and method.upper() != "GET":
        cmd.extend(["-X", method.upper()])
    if body is not None:
        cmd.extend(["-H", "Content-Type: application/json", "-d", body])
    for k, v in (headers or {}).items():
        cmd.extend(["-H", f"{k}: {v}"])
    cmd.append(url)
    return cmd


def curl(url, headers=None, timeout=30):
    """Run curl and return stdout or None."""
    cmd = _build_curl_cmd(url, headers)
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            creationflags=_CREATE_NO_WINDOW,
        )
        return r.stdout if r.returncode == 0 else None
    except Exception:
        return None


def curl_json(url, headers=None, timeout=30):
    """Run curl and parse JSON response."""
    body = curl(url, headers, timeout)
    if body:
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return None
    return None


def curl_post_json(url, data, headers=None, timeout=30):
    """POST JSON and parse response."""
    cmd = _build_curl_cmd(url, headers, method="POST", body=json.dumps(data))
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            creationflags=_CREATE_NO_WINDOW,
        )
        if r.returncode == 0:
            return json.loads(r.stdout)
    except Exception:
        pass
    return None


def http_head(url, timeout=20):
    """HEAD request returning (status, content_length, accepts_ranges).
    Follows redirects and parses the final response block."""
    cmd = ["curl", "-sI", "-L", "--max-time", str(timeout)]
    if NATIVE_PROXY:
        cmd.extend(["-x", NATIVE_PROXY])
    cmd.append(url)
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout + 5,
            creationflags=_CREATE_NO_WINDOW,
        )
        if r.returncode != 0:
            return (0, 0, False)
        blocks = [b for b in re.split(r"\r?\n\r?\n", r.stdout) if b.strip()]
        last = blocks[-1] if blocks else r.stdout
        status = 0
        m = re.search(r"HTTP/[\d.]+\s+(\d+)", last)
        if m:
            status = int(m.group(1))
        size = 0
        m = re.search(r"(?im)^content-length:\s*(\d+)", last)
        if m:
            size = int(m.group(1))
        accepts = bool(re.search(r"(?im)^accept-ranges:\s*bytes", last))
        return (status, size, accepts)
    except Exception:
        return (0, 0, False)


def parallel_http_download(url, outfile, connections=4, progress_cb=None,
                           cancel_check=None, min_size_mb=8, log_fn=None):
    """Download a direct HTTP file using N parallel Range requests.

    Probes with HEAD first. Returns False if the server lacks Range
    support, the file is too small to benefit from splitting, or any
    chunk fails — caller is expected to fall back to ffmpeg.
    """
    status, total, accepts = http_head(url)
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
    bytes_done = [0] * connections
    errors = []
    procs = {}
    lock = threading.Lock()

    def worker(i, start, end):
        part = part_paths[i]
        expected = end - start + 1
        if os.path.exists(part) and os.path.getsize(part) == expected:
            bytes_done[i] = expected
            return
        try:
            if os.path.exists(part):
                os.remove(part)
        except OSError:
            pass
        cmd = [
            "curl", "-sS", "-L", "--fail",
            "--retry", "2", "--retry-delay", "1",
            "-r", f"{start}-{end}",
            "-o", part,
        ]
        if NATIVE_PROXY:
            cmd.extend(["-x", NATIVE_PROXY])
        cmd.append(url)
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
                    err = (proc.stderr.read() or "").strip()[:120]
                except Exception:
                    pass
                errors.append(f"part {i}: curl exit {proc.returncode} {err}")
        except FileNotFoundError:
            errors.append("curl not in PATH")
        except Exception as e:
            errors.append(f"part {i}: {e}")

    threads = []
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

    if cancel_check and cancel_check():
        return False

    if errors:
        if log_fn:
            log_fn(f"[PARALLEL] failed: {'; '.join(errors[:3])}")
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
        return False

    for p in part_paths:
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            pass
    try:
        os.rmdir(tmp_dir)
    except OSError:
        pass

    if progress_cb:
        try:
            progress_cb(total, total, 0)
        except Exception:
            pass
    return True
