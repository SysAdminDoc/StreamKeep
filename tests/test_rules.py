"""Coverage for the ordered rules engine (V15) and its job integration."""

import unittest

from streamkeep import rules


def _rule(name="r", match=None, actions=None, **kw):
    base = {"name": name, "match": match or {}, "actions": actions or {}}
    base.update(kw)
    return base


class SiteFromUrlTests(unittest.TestCase):
    def test_strips_www_and_lowercases(self):
        self.assertEqual(rules.site_from_url("https://WWW.YouTube.com/watch?v=x"),
                         "youtube.com")

    def test_bad_url_is_empty(self):
        self.assertEqual(rules.site_from_url(""), "")
        self.assertEqual(rules.site_from_url("not a url"), "")


class ContextFromJobTests(unittest.TestCase):
    def test_derives_site_and_coerces_duration(self):
        ctx = rules.context_from_job({
            "url": "https://twitch.tv/foo", "title": "Big Stream",
            "channel": "foo", "duration": "3600",
        })
        self.assertEqual(ctx["site"], "twitch.tv")
        self.assertEqual(ctx["uploader"], "foo")
        self.assertEqual(ctx["duration"], 3600.0)

    def test_bad_duration_defaults_zero(self):
        ctx = rules.context_from_job({"url": "https://x/y", "duration": "abc"})
        self.assertEqual(ctx["duration"], 0.0)


class RuleMatchTests(unittest.TestCase):
    def setUp(self):
        self.ctx = rules.context_from_job({
            "url": "https://youtube.com/watch?v=1",
            "title": "Weekly Podcast Ep 5",
            "channel": "SomeCreator",
            "duration": 5400,
            "type": "video",
        })

    def test_empty_rule_never_matches(self):
        self.assertFalse(rules.rule_matches(_rule(), self.ctx))

    def test_site_substring_match(self):
        self.assertTrue(rules.rule_matches(_rule(match={"site": "youtube"}), self.ctx))
        self.assertFalse(rules.rule_matches(_rule(match={"site": "twitch"}), self.ctx))

    def test_title_regex_match_is_case_insensitive(self):
        self.assertTrue(rules.rule_matches(
            _rule(match={"title_regex": r"podcast ep \d+"}), self.ctx))

    def test_bad_regex_fails_closed(self):
        self.assertFalse(rules.rule_matches(
            _rule(match={"title_regex": "("}), self.ctx))

    def test_duration_bounds(self):
        self.assertTrue(rules.rule_matches(
            _rule(match={"duration_min": 3600}), self.ctx))
        self.assertFalse(rules.rule_matches(
            _rule(match={"duration_max": 60}), self.ctx))

    def test_type_exact(self):
        self.assertTrue(rules.rule_matches(_rule(match={"type": "video"}), self.ctx))
        self.assertFalse(rules.rule_matches(_rule(match={"type": "audio"}), self.ctx))

    def test_all_mode_requires_every_criterion(self):
        rule = _rule(match={"site": "youtube", "type": "audio"})
        self.assertFalse(rules.rule_matches(rule, self.ctx))

    def test_any_mode_requires_one_criterion(self):
        rule = _rule(match={"site": "youtube", "type": "audio"}, match_mode="any")
        self.assertTrue(rules.rule_matches(rule, self.ctx))


class EvaluateTests(unittest.TestCase):
    def setUp(self):
        self.ctx = rules.context_from_job({
            "url": "https://twitch.tv/foo", "title": "Late Night", "channel": "foo",
        })

    def test_no_rules_no_actions(self):
        self.assertEqual(rules.evaluate(self.ctx, []), {"actions": {}, "matched": []})

    def test_later_rule_overrides_earlier(self):
        ruleset = [
            _rule("a", match={"site": "twitch"}, actions={"output_dir": "/a"}),
            _rule("b", match={"site": "twitch"}, actions={"output_dir": "/b"}),
        ]
        out = rules.evaluate(self.ctx, ruleset)
        self.assertEqual(out["actions"]["output_dir"], "/b")
        self.assertEqual(out["matched"], ["a", "b"])

    def test_stop_halts_further_rules(self):
        ruleset = [
            _rule("a", match={"site": "twitch"}, actions={"output_dir": "/a"}, stop=True),
            _rule("b", match={"site": "twitch"}, actions={"output_dir": "/b"}),
        ]
        out = rules.evaluate(self.ctx, ruleset)
        self.assertEqual(out["actions"]["output_dir"], "/a")
        self.assertEqual(out["matched"], ["a"])

    def test_disabled_rules_are_skipped(self):
        ruleset = [_rule("a", match={"site": "twitch"},
                         actions={"output_dir": "/a"}, enabled=False)]
        self.assertEqual(rules.evaluate(self.ctx, ruleset)["actions"], {})

    def test_actions_are_coerced(self):
        ruleset = [_rule("a", match={"site": "twitch"}, actions={
            "priority": "7", "auto_start": 1, "proxy": "  http://p  ",
            "bogus": "ignored",
        })]
        actions = rules.evaluate(self.ctx, ruleset)["actions"]
        self.assertEqual(actions["priority"], 7)
        self.assertIs(actions["auto_start"], True)
        self.assertEqual(actions["proxy"], "http://p")
        self.assertNotIn("bogus", actions)


class NormalizeRuleTests(unittest.TestCase):
    def test_unknown_type_dropped_and_defaults_applied(self):
        norm = rules.normalize_rule({"match": {"type": "weird", "site": "x"}})
        self.assertNotIn("type", norm["match"])
        self.assertEqual(norm["match"]["site"], "x")
        self.assertTrue(norm["enabled"])
        self.assertEqual(norm["match_mode"], "all")

    def test_invalid_match_mode_falls_back(self):
        norm = rules.normalize_rule({"match_mode": "sometimes"})
        self.assertEqual(norm["match_mode"], "all")


class ApplyRulesToJobTests(unittest.TestCase):
    def test_no_rules_returns_copy_unchanged(self):
        job = {"url": "https://twitch.tv/foo"}
        out = rules.apply_rules_to_job(job, {})
        self.assertEqual(out, job)
        self.assertIsNot(out, job)

    def test_rule_fills_output_dir_and_template(self):
        config = {"rules": [_rule("a", match={"site": "twitch"}, actions={
            "output_dir": "/streams/twitch", "filename_template": "tmpl1",
            "proxy": "http://p", "priority": 5, "auto_start": True,
        })]}
        job = {"url": "https://twitch.tv/foo", "title": "x"}
        out = rules.apply_rules_to_job(job, config)
        self.assertEqual(out["output_dir"], "/streams/twitch")
        self.assertEqual(out["arg_template"], "tmpl1")
        self.assertEqual(out["proxy"], "http://p")
        self.assertEqual(out["priority"], 5)
        self.assertIs(out["auto_start"], True)
        self.assertEqual(out["_rule_matched"], ["a"])

    def test_explicit_job_value_is_not_clobbered(self):
        config = {"rules": [_rule("a", match={"site": "twitch"},
                                  actions={"output_dir": "/from-rule"})]}
        job = {"url": "https://twitch.tv/foo", "output_dir": "/user-set"}
        out = rules.apply_rules_to_job(job, config)
        self.assertEqual(out["output_dir"], "/user-set")

    def test_non_matching_rule_leaves_job_alone(self):
        config = {"rules": [_rule("a", match={"site": "vimeo"},
                                  actions={"output_dir": "/x"})]}
        job = {"url": "https://twitch.tv/foo"}
        out = rules.apply_rules_to_job(job, config)
        self.assertNotIn("output_dir", out)
        self.assertNotIn("_rule_actions", out)


if __name__ == "__main__":
    unittest.main()
