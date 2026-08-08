"""Every outbound request must be accounted for.

``net_guard`` compliance used to be a per-call-site convention, which is exactly
why ``intelligence/summarize.py`` shipped an API key over cleartext http and
three unbounded reads while ``translation.py`` next door had the same code
correct (V183). Nothing in the suite would have noticed the next one.

So the set of modules that open a socket is derived from the source and checked
against an allowlist that states *why* each one is allowed to bypass the guarded
transport. A new outbound call in a module that is not listed fails this test,
and the reason has to be written down rather than assumed (V188).
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "streamkeep"

#: Direct-socket call sites and the reason each is not routed through
#: ``net_guard``. "Guarded" means the module already validates the address and
#: proxies the connection itself.
_ALLOWED = {
    "streamkeep/net_guard.py":
        "implements the guarded transport itself",
    "streamkeep/scrape.py":
        "pins the resolved address on its own connection class; net_guard was "
        "factored out of this module",
    "streamkeep/image_fetch.py":
        "pins the validated address on its own connection classes",
    "streamkeep/chat/kick_ws.py":
        "wss to a fixed Pusher host discovered from Kick's own page; not a "
        "user-supplied URL",
    "streamkeep/chat/twitch_irc.py":
        "TLS IRC to the fixed host irc.chat.twitch.tv:6697 with certificate "
        "and hostname verification",
    "streamkeep/extractors/twitch_recover.py":
        "probes candidate CDN hosts derived from the platform's own naming "
        "scheme; reads are bounded",
    "streamkeep/integrations/media_server.py":
        "the media-server address is explicitly configured by the operator, so "
        "SSRF is not the threat; the read is bounded",
    "streamkeep/intelligence/runtime.py":
        "fixed loopback capability probe against 127.0.0.1 with a bounded read",
    "streamkeep/intelligence/summarize.py":
        "the local Ollama endpoint is loopback and unauthenticated, so it is "
        "addressed directly with a bounded read; both cloud providers go "
        "through net_guard",
    "streamkeep/translation.py":
        "same as summarize: loopback Ollama direct and bounded, cloud "
        "providers guarded",
    "streamkeep/postprocess/emote_cache.py":
        "fixed third-party emote provider hosts with bounded reads and a cache "
        "quota",
    "streamkeep/pot_provider.py":
        "loopback reachability probe for a locally running provider",
    "streamkeep/updater.py":
        "GitHub release API and asset download over a fixed host, bounded by "
        "_read_limited and verified by update_security before use",
    "streamkeep/upload/webdav.py":
        "the WebDAV destination is explicitly configured by the operator and "
        "https is required unless allow_insecure_http is set",
}

_ATTRIBUTE_TARGETS = {
    ("urllib.request", "urlopen"),
    ("socket", "create_connection"),
    ("socket", "socket"),
    ("websocket", "create_connection"),
}
_CONNECTION_CLASSES = {"HTTPConnection", "HTTPSConnection"}


def _modules_opening_sockets():
    found = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        sites = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute):
                owner = ast.unparse(func.value)
                if (owner, func.attr) in _ATTRIBUTE_TARGETS:
                    sites.append(node.lineno)
                elif func.attr in _CONNECTION_CLASSES:
                    sites.append(node.lineno)
            elif isinstance(func, ast.Name) and func.id in _CONNECTION_CLASSES:
                sites.append(node.lineno)
        if sites:
            key = str(path.relative_to(ROOT)).replace("\\", "/")
            found[key] = sorted(sites)
    return found


def test_every_module_opening_a_socket_states_why():
    """A new direct-socket call must be justified, not merely written."""
    found = _modules_opening_sockets()
    unexplained = sorted(set(found) - set(_ALLOWED))
    assert not unexplained, (
        "these modules open a socket without going through net_guard and "
        "without a stated reason in tests/test_egress_boundaries.py: "
        + ", ".join(f"{path} (lines {found[path]})" for path in unexplained)
    )


def test_the_allowlist_has_no_stale_entries():
    """An entry that no longer opens a socket must be removed, not left."""
    found = _modules_opening_sockets()
    stale = sorted(set(_ALLOWED) - set(found))
    assert not stale, (
        "these allowlist entries no longer open a socket and should be "
        f"deleted: {stale}"
    )


def test_every_allowlist_entry_gives_a_real_reason():
    for path, reason in _ALLOWED.items():
        assert len(reason) > 25, f"{path}: the reason is too short to be one"


def test_the_cloud_summary_providers_do_not_appear_as_direct_callers():
    """summarize.py may only reach loopback directly.

    The regression this guards is V183: the OpenAI-compatible and Anthropic
    paths posting an API key straight out through urlopen.
    """
    source = (PACKAGE / "intelligence" / "summarize.py").read_text(
        encoding="utf-8",
    )
    tree = ast.parse(source)
    direct = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and ast.unparse(node.func.value) == "urllib.request"
                and node.func.attr == "urlopen"):
            direct.append(node.lineno)
    assert len(direct) == 1, (
        "summarize.py should have exactly one direct urlopen -- the loopback "
        f"Ollama call -- but has {len(direct)} at lines {direct}"
    )
    assert "guarded_json_post" in source
    assert "require_https_endpoint" in source
