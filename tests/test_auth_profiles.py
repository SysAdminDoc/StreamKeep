"""Named, site-bound authentication profiles (V50).

The central property under test is that credential material follows a
profile's declared scope and never falls back across sites.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from streamkeep import auth_profiles as ap

NETSCAPE = (
    "# Netscape HTTP Cookie File\n"
    ".youtube.com\tTRUE\t/\tTRUE\t2000000000\tSID\tsecret-youtube-value\n"
    ".google.com\tTRUE\t/\tTRUE\t2000000000\tHSID\tsecret-google-value\n"
)
TWITCH_NETSCAPE = (
    "# Netscape HTTP Cookie File\n"
    ".twitch.tv\tTRUE\t/\tTRUE\t2000000000\tauth-token\tsecret-twitch-value\n"
)


class _ProfileDirCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.auth_dir = self.root / "auth"
        for attr, value in (
            ("AUTH_DIR", self.auth_dir),
            ("INDEX_FILE", self.auth_dir / "profiles.json"),
        ):
            patcher = mock.patch.object(ap, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _profile_with_cookies(self, name="YouTube", hosts=("youtube.com",),
                              text=NETSCAPE):
        profile = ap.create_profile(name, hosts=hosts)
        path = self.root / f"{name}-source.txt"
        path.write_text(text, encoding="utf-8")
        ok, message = ap.import_from_file(profile.profile_id, str(path))
        self.assertTrue(ok, message)
        return ap.get_profile(profile.profile_id)


class HostNormalizationTests(unittest.TestCase):
    def test_urls_dots_and_wildcards_reduce_to_a_bare_host(self):
        cases = {
            "https://www.YouTube.com/watch?v=x": "youtube.com",
            ".twitch.tv": "twitch.tv",
            "*.kick.com": "kick.com",
            "example.co.uk:8443": "example.co.uk",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(ap.normalize_host(value), expected)

    def test_values_that_are_not_hosts_are_dropped(self):
        for value in ("", "   ", "localhost", "not a host", "..", "http://"):
            with self.subTest(value=value):
                self.assertEqual(ap.normalize_host(value), "")

    def test_scope_matching_is_label_aware(self):
        allowed = ("youtube.com",)
        self.assertTrue(ap.host_in_scope("youtube.com", allowed))
        self.assertTrue(ap.host_in_scope("music.youtube.com", allowed))
        # A lookalike registrable domain must never match.
        self.assertFalse(ap.host_in_scope("evil-youtube.com", allowed))
        self.assertFalse(ap.host_in_scope("youtube.com.attacker.net", allowed))
        self.assertFalse(ap.host_in_scope("twitch.tv", allowed))


class ProfileLifecycleTests(_ProfileDirCase):
    def test_a_profile_must_declare_a_scope(self):
        with self.assertRaises(ap.AuthProfileError):
            ap.create_profile("Unscoped")

    def test_duplicate_names_are_refused(self):
        ap.create_profile("YouTube", hosts=("youtube.com",))
        with self.assertRaises(ap.AuthProfileError):
            ap.create_profile("youtube", hosts=("youtu.be",))

    def test_profile_ids_are_opaque_and_unique(self):
        first = ap.create_profile("YouTube Members", hosts=("youtube.com",))
        second = ap.create_profile("Twitch Subs", hosts=("twitch.tv",))
        self.assertNotEqual(first.profile_id, second.profile_id)
        for profile in (first, second):
            self.assertTrue(profile.profile_id.startswith("ap_"))
            self.assertNotIn(
                profile.name.lower().replace(" ", ""), profile.profile_id,
            )
            self.assertNotIn("youtube", profile.profile_id)
            self.assertNotIn("twitch", profile.profile_id)

    def test_profiles_and_scope_survive_a_reload(self):
        created = ap.create_profile(
            "Members", hosts=("youtube.com", "www.YouTu.be"), platforms=("YouTube",),
        )
        loaded = ap.get_profile(created.profile_id)
        self.assertEqual(loaded.hosts, ("youtu.be", "youtube.com"))
        self.assertEqual(loaded.platforms, ("youtube",))

    def test_find_profile_accepts_a_name_or_an_id(self):
        created = ap.create_profile("Members", hosts=("youtube.com",))
        self.assertEqual(ap.find_profile("Members"), created)
        self.assertEqual(ap.find_profile("members"), created)
        self.assertEqual(ap.find_profile(created.profile_id), created)
        self.assertIsNone(ap.find_profile("nope"))

    def test_deleting_a_profile_shreds_its_material(self):
        profile = self._profile_with_cookies()
        path = ap.cookies_path(profile.profile_id)
        self.assertTrue(os.path.isfile(path))
        self.assertTrue(ap.delete_profile(profile.profile_id))
        self.assertFalse(os.path.exists(path))
        self.assertIsNone(ap.get_profile(profile.profile_id))

    def test_updating_scope_cannot_leave_a_profile_unscoped(self):
        profile = ap.create_profile("Members", hosts=("youtube.com",))
        with self.assertRaises(ap.AuthProfileError):
            ap.update_profile(profile.profile_id, hosts=(), platforms=())

    def test_a_corrupt_index_is_treated_as_empty_rather_than_raising(self):
        self.auth_dir.mkdir(parents=True, exist_ok=True)
        (self.auth_dir / "profiles.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(ap.list_profiles(), [])


class MaterialStorageTests(_ProfileDirCase):
    def test_material_lives_beside_the_profile_and_not_in_the_index(self):
        profile = self._profile_with_cookies()
        index = json.loads((self.auth_dir / "profiles.json").read_text("utf-8"))
        self.assertNotIn("secret-youtube-value", json.dumps(index))
        stored = Path(ap.cookies_path(profile.profile_id)).read_text("utf-8")
        self.assertIn("secret-youtube-value", stored)

    def test_a_non_netscape_file_is_refused(self):
        profile = ap.create_profile("YouTube", hosts=("youtube.com",))
        path = self.root / "not-cookies.txt"
        path.write_text("just some text", encoding="utf-8")
        ok, message = ap.import_from_file(profile.profile_id, str(path))
        self.assertFalse(ok)
        self.assertIn("Netscape", message)
        self.assertEqual(ap.cookies_path(profile.profile_id), "")

    def test_clearing_credentials_keeps_the_profile(self):
        profile = self._profile_with_cookies()
        self.assertTrue(ap.clear_credentials(profile.profile_id))
        self.assertEqual(ap.cookies_path(profile.profile_id), "")
        self.assertIsNotNone(ap.get_profile(profile.profile_id))

    def test_public_view_reveals_no_secret_material_or_paths(self):
        profile = self._profile_with_cookies()
        view = ap.public_view(profile)
        rendered = json.dumps(view)
        self.assertNotIn("secret-youtube-value", rendered)
        self.assertNotIn(str(self.auth_dir), rendered)
        self.assertTrue(view["has_credentials"])
        self.assertEqual(view["hosts"], ["youtube.com"])


class ScopeEnforcementTests(_ProfileDirCase):
    def test_a_profile_is_used_only_inside_its_declared_scope(self):
        profile = self._profile_with_cookies()
        self.assertEqual(
            ap.resolve_profile("https://www.youtube.com/watch?v=x"), profile,
        )
        self.assertEqual(
            ap.resolve_profile("https://music.youtube.com/watch?v=x"), profile,
        )
        self.assertIsNone(ap.resolve_profile("https://www.twitch.tv/some"))

    def test_naming_a_profile_never_authorizes_a_different_site(self):
        profile = self._profile_with_cookies()
        # Explicitly requested, but the URL is out of scope: no credential.
        self.assertIsNone(
            ap.resolve_profile(
                "https://www.twitch.tv/some", profile_id=profile.profile_id,
            )
        )
        self.assertEqual(
            ap.resolve_cookies_path(
                "https://www.twitch.tv/some", profile_id=profile.profile_id,
            ),
            "",
        )

    def test_a_lookalike_host_gets_no_credential(self):
        self._profile_with_cookies()
        self.assertEqual(
            ap.resolve_cookies_path("https://evil-youtube.com/watch?v=x"), "",
        )

    def test_platform_scope_authorizes_when_the_host_is_unknown(self):
        profile = ap.create_profile("Kick", platforms=("kick",))
        self.assertEqual(ap.resolve_profile("", "Kick"), profile)
        self.assertIsNone(ap.resolve_profile("", "Twitch"))

    def test_two_covering_profiles_send_nothing_rather_than_guessing(self):
        self._profile_with_cookies("First", ("youtube.com",))
        self._profile_with_cookies("Second", ("youtube.com",))
        self.assertIsNone(ap.resolve_profile("https://youtube.com/watch?v=x"))
        # Naming one of them resolves the ambiguity explicitly.
        named = ap.find_profile("Second")
        self.assertEqual(
            ap.resolve_profile(
                "https://youtube.com/watch?v=x", profile_id="Second",
            ),
            named,
        )

    def test_each_site_gets_only_its_own_jar(self):
        youtube = self._profile_with_cookies("YouTube", ("youtube.com",))
        twitch = self._profile_with_cookies(
            "Twitch", ("twitch.tv",), TWITCH_NETSCAPE,
        )
        yt_path = ap.resolve_cookies_path("https://youtube.com/watch?v=x")
        tw_path = ap.resolve_cookies_path("https://www.twitch.tv/x")
        self.assertEqual(yt_path, ap.cookies_path(youtube.profile_id))
        self.assertEqual(tw_path, ap.cookies_path(twitch.profile_id))
        self.assertNotIn(
            "secret-twitch-value", Path(yt_path).read_text("utf-8"),
        )
        self.assertNotIn(
            "secret-youtube-value", Path(tw_path).read_text("utf-8"),
        )

    def test_a_profile_without_material_yields_no_cookie_path(self):
        profile = ap.create_profile("Empty", hosts=("youtube.com",))
        # The profile still covers the URL, but there is nothing to send.
        self.assertEqual(ap.resolve_profile("https://youtube.com/x"), profile)
        self.assertEqual(ap.resolve_cookies_path("https://youtube.com/x"), "")


class CommandWiringTests(_ProfileDirCase):
    def test_ytdlp_attaches_only_the_covering_profile(self):
        from streamkeep.extractors.ytdlp import ytdlp_auth_args

        profile = self._profile_with_cookies()
        args = ytdlp_auth_args("https://www.youtube.com/watch?v=x")
        self.assertEqual(args, ["--cookies", ap.cookies_path(profile.profile_id)])
        self.assertEqual(ytdlp_auth_args("https://www.twitch.tv/x"), [])

    def test_ytdlp_legacy_fallback_only_applies_without_a_profile(self):
        from streamkeep.extractors.ytdlp import ytdlp_auth_args

        legacy = self.root / "legacy.txt"
        legacy.write_text(NETSCAPE, encoding="utf-8")
        self.assertEqual(
            ytdlp_auth_args("https://example.com/x", cookie_file=str(legacy)),
            ["--cookies", str(legacy)],
        )
        self.assertEqual(
            ytdlp_auth_args("https://example.com/x", browser="firefox"),
            ["--cookies-from-browser", "firefox"],
        )
        # A covering profile wins over the legacy jar.
        profile = self._profile_with_cookies()
        self.assertEqual(
            ytdlp_auth_args(
                "https://youtube.com/x", cookie_file=str(legacy), browser="firefox",
            ),
            ["--cookies", ap.cookies_path(profile.profile_id)],
        )

    def test_curl_attaches_only_the_covering_profile(self):
        from streamkeep import http

        profile = self._profile_with_cookies()
        cmd = []
        http._append_cookie_args(cmd, "https://www.youtube.com/watch?v=x")
        self.assertEqual(cmd, ["--cookie", ap.cookies_path(profile.profile_id)])
        other = []
        with mock.patch("streamkeep.cookies.cookies_file_path", return_value=""):
            http._append_cookie_args(other, "https://www.twitch.tv/x")
        self.assertEqual(other, [])

    def test_the_job_spec_carries_only_an_opaque_reference(self):
        from streamkeep.job_spec import DownloadJobSpec

        profile = self._profile_with_cookies()
        spec = DownloadJobSpec(auth_profile_id=profile.profile_id)
        rendered = json.dumps(spec.to_dict())
        self.assertIn(profile.profile_id, rendered)
        self.assertNotIn("secret-youtube-value", rendered)
        self.assertNotIn(str(self.auth_dir), rendered)

    def test_rules_can_select_a_profile_by_name(self):
        from streamkeep.rules import apply_rules_to_job

        config = {"rules": [{
            "name": "members",
            "enabled": True,
            "match": {"site": "youtube.com"},
            "actions": {"auth_profile": "Members"},
        }]}
        job = apply_rules_to_job(
            {"url": "https://www.youtube.com/watch?v=x"}, config,
        )
        self.assertEqual(job["auth_profile_id"], "Members")
        other = apply_rules_to_job({"url": "https://www.twitch.tv/x"}, config)
        self.assertNotIn("auth_profile_id", other)


class RedactionTests(_ProfileDirCase):
    def test_a_config_export_drops_the_profile_reference(self):
        from streamkeep.config import export_config

        exported = export_config({
            "auth_profile_id": "ap_0123456789abcdef",
            "cookies_browser": "firefox",
            "output_dir": r"C:\videos",
        })
        self.assertEqual(exported.get("auth_profile_id", ""), "")
        self.assertEqual(exported.get("cookies_browser", ""), "")

    def test_a_backup_never_contains_the_profile_directory(self):
        from streamkeep import backup

        self._profile_with_cookies()
        self.assertNotIn("auth", backup.BACKUP_FILES)
        self.assertTrue(
            all(not name.startswith("auth") for name in backup.BACKUP_FILES)
        )


class MigrationTests(_ProfileDirCase):
    def test_the_legacy_jar_moves_into_an_explicit_default_profile(self):
        legacy = self.root / "cookies.txt"
        legacy.write_text(NETSCAPE, encoding="utf-8")
        config = {"cookies_browser": "firefox", "cookies_file": ""}
        with mock.patch(
            "streamkeep.cookies.cookies_file_path", return_value=str(legacy),
        ):
            profile = ap.migrate_global_cookies(config)

        self.assertIsNotNone(profile)
        self.assertEqual(profile.name, ap.DEFAULT_PROFILE_NAME)
        # Scope came from the jar's own domains, not a blanket allow.
        self.assertEqual(profile.hosts, ("google.com", "youtube.com"))
        # Moved, not copied: material exists once.
        self.assertFalse(legacy.exists())
        stored = Path(ap.cookies_path(profile.profile_id)).read_text("utf-8")
        self.assertIn("secret-youtube-value", stored)
        # The global setting is retired and replaced by the opaque reference.
        self.assertEqual(config["cookies_browser"], "")
        self.assertEqual(config["auth_profile_id"], profile.profile_id)
        self.assertTrue(config[ap.MIGRATED_KEY])

    def test_migration_runs_once(self):
        legacy = self.root / "cookies.txt"
        legacy.write_text(NETSCAPE, encoding="utf-8")
        config = {}
        with mock.patch(
            "streamkeep.cookies.cookies_file_path", return_value=str(legacy),
        ):
            first = ap.migrate_global_cookies(config)
            second = ap.migrate_global_cookies(config)
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(len(ap.list_profiles()), 1)

    def test_nothing_to_migrate_still_marks_the_profile_done(self):
        config = {}
        with mock.patch("streamkeep.cookies.cookies_file_path", return_value=""):
            self.assertIsNone(ap.migrate_global_cookies(config))
        self.assertTrue(config[ap.MIGRATED_KEY])
        self.assertEqual(ap.list_profiles(), [])

    def test_existing_profiles_are_never_overwritten_by_migration(self):
        ap.create_profile("Mine", hosts=("twitch.tv",))
        legacy = self.root / "cookies.txt"
        legacy.write_text(NETSCAPE, encoding="utf-8")
        config = {}
        with mock.patch(
            "streamkeep.cookies.cookies_file_path", return_value=str(legacy),
        ):
            self.assertIsNone(ap.migrate_global_cookies(config))
        self.assertEqual([p.name for p in ap.list_profiles()], ["Mine"])
