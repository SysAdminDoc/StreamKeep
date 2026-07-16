from streamkeep.clipboard import extract_url


def test_strips_trailing_prose_punctuation():
    assert extract_url("see https://example.com/video.") == "https://example.com/video"
    assert extract_url("(https://example.com/x)") == "https://example.com/x"
    assert extract_url("link: https://example.com/a,") == "https://example.com/a"
    assert extract_url("https://example.com/a!?") == "https://example.com/a"


def test_keeps_balanced_brackets_in_url():
    url = "https://en.wikipedia.org/wiki/Example_(disambiguation)"
    assert extract_url(f"see {url}") == url


def test_extracts_from_first_nonempty_line_only():
    assert extract_url("\n\nhere https://example.com/first\nhttps://second") == (
        "https://example.com/first"
    )


def test_no_url_returns_empty():
    assert extract_url("no links here") == ""
    assert extract_url("") == ""
    assert extract_url(None) == ""


def test_query_and_path_preserved():
    url = "https://example.com/watch?v=abc123&t=10"
    assert extract_url(url) == url
