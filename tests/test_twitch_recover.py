import unittest
from unittest import mock

from streamkeep.extractors import twitch_recover


class TwitchRecoverValidationTests(unittest.TestCase):
    def test_invalid_channel_short_circuits_before_network(self):
        logs = []
        with mock.patch.object(
            twitch_recover, "_scrape_twitchtracker"
        ) as scrape:
            for bad in ["", "  ", "ab", "foo/../../evil", "x@evil.com", "a" * 26,
                        "has space", "semi;colon"]:
                result = twitch_recover.recover_channel_vods(
                    bad, 2024, 1, log_fn=logs.append
                )
                self.assertEqual(result, [])
            scrape.assert_not_called()

    def test_valid_channel_proceeds_to_scrape(self):
        with mock.patch.object(
            twitch_recover, "_scrape_twitchtracker", return_value=[]
        ) as scrape:
            result = twitch_recover.recover_channel_vods("Good_Streamer1", 2024, 1)
            self.assertEqual(result, [])
            scrape.assert_called_once()


if __name__ == "__main__":
    unittest.main()
