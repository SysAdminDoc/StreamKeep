"""V162: a throttle from one job must slow the whole queue for that host.

The policy is exercised two ways. Most tests drive the governor directly with
an injected clock, because the interesting behaviour is a state machine and a
real clock would only make it slow and flaky. One test stands a real HTTP
server up that returns 429 with a Retry-After, so the signal the governor acts
on is one an actual host produced rather than one the test invented.
"""

import http.server
import threading
import urllib.error
import urllib.request

import pytest

from streamkeep import governor


@pytest.fixture(autouse=True)
def clean_governor():
    governor.reset()
    governor.configure(enabled=True, default_concurrency=4)
    yield
    governor.reset()
    governor.configure(enabled=True, default_concurrency=governor.DEFAULT_CONCURRENCY)


HOST = "https://cdn.example.com/live/master.m3u8"
OTHER = "https://other.example.net/video.mp4"


# ── Host identity ───────────────────────────────────────────────────

@pytest.mark.parametrize("value, expected", [
    ("https://CDN.Example.com/a", "cdn.example.com"),
    ("http://cdn.example.com:8080/a", "cdn.example.com"),
    ("cdn.example.com", "cdn.example.com"),
    ("CDN.example.com.", "cdn.example.com"),
    ("", ""),
    (None, ""),
])
def test_host_key_folds_to_one_conversation_per_host(value, expected):
    assert governor.host_key(value) == expected


# ── Backing off ─────────────────────────────────────────────────────

def test_a_throttle_halves_concurrency_and_starts_a_delay():
    assert governor.concurrency_for(HOST) == 4
    assert governor.delay_for(HOST) == 0

    governor.record_throttle(HOST)

    assert governor.concurrency_for(HOST) == 2
    assert governor.delay_for(HOST) == governor.BASE_DELAY_SECONDS


def test_repeated_throttles_back_off_further_but_never_stop_the_queue():
    for _ in range(10):
        governor.record_throttle(HOST)

    assert governor.concurrency_for(HOST) == governor.MIN_CONCURRENCY >= 1
    assert governor.delay_for(HOST) <= governor.MAX_DELAY_SECONDS


def test_a_retry_after_header_outranks_the_computed_delay():
    governor.record_throttle(HOST, retry_after=90)

    assert governor.delay_for(HOST) == 90


def test_a_long_retry_after_is_honoured_literally_and_keeps_its_class():
    state = governor.record_throttle(
        HOST, retry_after=600, reason="bot-check failure",
        classification="bot-check",
    )

    assert state.delay_seconds == 600
    assert state.classification == "bot-check"
    assert governor.public_view()["hosts"][0]["classification"] == "bot-check"


def test_a_smaller_retry_after_does_not_shorten_an_earned_backoff():
    """A host asking for 1s after we already backed off 4 times is not a reset."""
    for _ in range(4):
        governor.record_throttle(HOST)
    earned = governor.delay_for(HOST)

    governor.record_throttle(HOST, retry_after=1)

    assert governor.delay_for(HOST) >= earned


@pytest.mark.parametrize("bogus", ["soon", None, -5, float("nan")])
def test_an_unusable_retry_after_falls_back_to_the_computed_delay(bogus):
    governor.record_throttle(HOST, retry_after=bogus)

    assert governor.delay_for(HOST) >= governor.BASE_DELAY_SECONDS


# ── The queue-wide property ─────────────────────────────────────────

def test_one_job_throttling_slows_every_job_aimed_at_that_host():
    """This is the whole item: the reaction is per host, not per job."""
    governor.record_throttle(HOST)

    assert governor.concurrency_for(HOST) == 2
    assert governor.delay_for(HOST) > 0


def test_a_throttle_does_not_slow_an_unrelated_host():
    governor.record_throttle(HOST)

    assert governor.concurrency_for(OTHER) == 4
    assert governor.delay_for(OTHER) == 0


def test_the_ceiling_still_wins_over_the_governor():
    """The governor may only ever slow things down, never speed them up."""
    assert governor.concurrency_for(HOST, ceiling=1) == 1
    governor.record_throttle(HOST)
    assert governor.concurrency_for(HOST, ceiling=8) == 2


def test_disabling_the_governor_stops_it_advising():
    governor.record_throttle(HOST)
    governor.configure(enabled=False, default_concurrency=4)

    assert governor.concurrency_for(HOST, ceiling=4) == 4
    assert governor.delay_for(HOST) == 0


# ── Recovering ──────────────────────────────────────────────────────

def test_recovery_needs_sustained_success_not_a_single_one():
    governor.record_throttle(HOST)
    governor.record_success(HOST)

    assert governor.concurrency_for(HOST) == 2, "one success is not recovery"

    for _ in range(governor.SUCCESSES_TO_RECOVER - 1):
        governor.record_success(HOST)

    assert governor.concurrency_for(HOST) == 3


def test_sustained_success_eventually_returns_the_host_to_normal():
    governor.record_throttle(HOST)
    governor.record_throttle(HOST)

    for _ in range(governor.SUCCESSES_TO_RECOVER * 8):
        governor.record_success(HOST)

    assert governor.concurrency_for(HOST) == 4
    assert governor.delay_for(HOST) == 0
    assert governor.state_for(HOST).throttled is False


def test_a_quiet_host_recovers_without_any_traffic_at_all():
    """Otherwise one 429 in an overnight backfill throttles the morning queue."""
    start = 1_000_000.0
    governor.record_throttle(HOST, now=start)
    assert governor.concurrency_for(HOST, now=start) == 2

    later = start + governor.IDLE_RESET_SECONDS + 1
    assert governor.concurrency_for(HOST, now=later) == 4
    assert governor.delay_for(HOST, now=later) == 0


# ── What the operator sees ──────────────────────────────────────────

def test_an_unthrottled_queue_reports_nothing():
    assert governor.summary() == ""
    assert governor.public_view()["hosts"] == []


def test_the_active_state_names_the_host_and_the_limit():
    governor.record_throttle(HOST, retry_after=30)

    text = governor.summary()
    assert "cdn.example.com" in text
    assert "30" in text

    view = governor.public_view()
    assert view["enabled"] is True
    assert view["hosts"][0]["host"] == "cdn.example.com"
    assert view["hosts"][0]["delay_seconds"] == 30
    assert view["hosts"][0]["throttles"] == 1


def test_the_public_view_carries_no_urls_or_paths():
    governor.record_throttle("https://cdn.example.com/secret/path?token=abc123")

    view = governor.public_view()
    blob = repr(view)
    assert "secret" not in blob and "token" not in blob and "abc123" not in blob


# ── Against a real throttling server ────────────────────────────────

class _ThrottlingHandler(http.server.BaseHTTPRequestHandler):
    """Returns 429 with a Retry-After, the way a rate-limiting host does."""

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's spelling
        self.send_response(429)
        self.send_header("Retry-After", "42")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass  # keep the test output clean


@pytest.fixture
def throttling_server():
    server = http.server.HTTPServer(("127.0.0.1", 0), _ThrottlingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/media.m3u8"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_a_real_429_is_classified_and_governs_the_host(throttling_server):
    """End to end: a real response -> the shipped classifier -> the governor."""
    from streamkeep.retry import classify_failure

    try:
        urllib.request.urlopen(throttling_server, timeout=5)
        raise AssertionError("the server was supposed to refuse")
    except urllib.error.HTTPError as error:
        retry_after = error.headers.get("Retry-After")
        decision = classify_failure(f"HTTP Error {error.code}: Too Many Requests")

    assert decision.category == "rate_limit", decision
    assert retry_after == "42"

    before = governor.concurrency_for(throttling_server)
    governor.record_throttle(throttling_server, retry_after=retry_after)

    assert governor.concurrency_for(throttling_server) < before
    assert governor.delay_for(throttling_server) == 42


# ── The queue honours the advice ────────────────────────────────────

class _Queue:
    """Only what the deferral decision reads."""

    def __init__(self, max_concurrent=4, running=(), last_start=None):
        self.max_concurrent = max_concurrent
        self._job_hosts = {f"job{i}": host for i, host in enumerate(running)}
        self._host_last_start = dict(last_start or {})


def _defers(queue, url):
    from streamkeep.headless_service import HeadlessJobService

    return HeadlessJobService._governor_defers(queue, {"url": url})


def test_a_healthy_host_is_not_deferred():
    assert _defers(_Queue(), HOST) == ""


def test_a_job_is_deferred_once_its_host_is_at_the_governed_limit():
    governor.record_throttle(HOST)          # allowance drops to 2

    assert _defers(_Queue(running=["cdn.example.com"]), HOST) == ""
    held = _defers(_Queue(running=["cdn.example.com"] * 2), HOST)
    assert "limited to 2" in held


def test_running_jobs_on_other_hosts_do_not_count_against_this_one():
    governor.record_throttle(HOST)

    queue = _Queue(running=["other.example.net"] * 4)

    assert _defers(queue, HOST) == ""


def test_a_job_is_deferred_until_the_pacing_delay_has_elapsed():
    import time as _time

    governor.record_throttle(HOST, retry_after=60)
    just_started = _Queue(last_start={"cdn.example.com": _time.time()})
    long_ago = _Queue(last_start={"cdn.example.com": _time.time() - 3600})

    assert "paced at 60s" in _defers(just_started, HOST)
    assert _defers(long_ago, HOST) == ""


def test_a_job_with_no_usable_host_is_never_deferred():
    """A malformed row must not become a job that can never start."""
    assert _defers(_Queue(), "") == ""
    assert _defers(_Queue(), "not a url") == ""


def test_the_dispatch_loop_actually_consults_the_governor(monkeypatch):
    """The deferral tests above call the method directly, so they stay green
    even if the queue stops calling it. This drives the real dispatch loop."""
    from streamkeep import headless_service as service_module
    from streamkeep.headless_service import HeadlessJobService

    jobs = [
        {"job_id": "a", "url": HOST},
        {"job_id": "b", "url": HOST},
        {"job_id": "c", "url": OTHER},
    ]
    started = []

    class _FakeDb:
        @staticmethod
        def promote_due_failed_jobs(_owner):
            return []

        @staticmethod
        def skip_tombstoned_queue_jobs():
            return []

        @staticmethod
        def load_queue_by_status(_status):
            return list(jobs)

    monkeypatch.setattr(service_module, "db", _FakeDb)

    class _Service:
        """A plain stand-in. HeadlessJobService is a QObject, and attribute
        assignment on an uninitialised one reaches Qt's property machinery and
        raises RuntimeError, so the real class cannot be hollowed out here."""

        max_concurrent = 4
        owner_id = "test-owner"
        _started = True
        _stopping = False

        def __init__(self):
            self._fetchers = {}
            self._downloads = {}
            self._finalizers = {}
            self._job_hosts = {}
            self._host_last_start = {}

        def _governor_defers(self, job):
            return HeadlessJobService._governor_defers(self, job)

        _eligible = staticmethod(HeadlessJobService._eligible)

        def _start_fetch(self, job):
            started.append(job["job_id"])

        def _forget_request_headers(self, job_id):
            pass

    service = _Service()

    # cdn.example.com is throttled down to one job at a time and already has
    # one in flight; other.example.net is healthy.
    governor.record_throttle(HOST)
    governor.record_throttle(HOST)
    assert governor.concurrency_for(HOST) == 1
    service._job_hosts["already-running"] = governor.host_key(HOST)

    HeadlessJobService._dispatch(service)

    assert "a" not in started and "b" not in started, (
        "the dispatch loop started a job for a host that is at its limit"
    )
    assert "c" in started, "an unrelated healthy host must not be held back"


# ── The operator can see and switch it off ──────────────────────────

class _SettingsPane:
    """The Settings surface reduced to what the governor handlers touch."""

    def __init__(self, config=None):
        self._config = dict(config or {})
        self._status = []
        self.rate_governor_status = _Label()

    def _set_status(self, text, level="info"):
        self._status.append((level, text))


class _Label:
    def __init__(self):
        self.text_value = ""

    def setText(self, value):
        self.text_value = value


def _refresh(pane):
    from streamkeep.ui.tabs.settings_tools import SettingsToolsMixin

    return SettingsToolsMixin._refresh_rate_governor_ui(pane)


def test_the_settings_panel_reports_a_quiet_queue():
    pane = _SettingsPane()

    _refresh(pane)

    assert "No host is being throttled" in pane.rate_governor_status.text_value


def test_the_settings_panel_names_the_host_it_is_backing_off_from():
    governor.record_throttle(HOST, retry_after=45)
    pane = _SettingsPane()

    _refresh(pane)

    text = pane.rate_governor_status.text_value
    assert "cdn.example.com" in text
    assert "45s" in text


def test_turning_it_off_is_reflected_in_what_the_panel_says():
    governor.record_throttle(HOST)
    governor.configure(enabled=False, default_concurrency=4)
    pane = _SettingsPane()

    _refresh(pane)

    assert "off" in pane.rate_governor_status.text_value.casefold()
