import unittest

from rtve_dl.subtitle_tracks.policy import enabled_ru_track_ids, parse_track_policy


class TrackPolicyTests(unittest.TestCase):
    def test_defaults(self) -> None:
        p = parse_track_policy([])
        self.assertEqual(p.mode("es"), "on")
        self.assertEqual(p.mode("en"), "on")
        self.assertEqual(p.mode("ru"), "require")
        self.assertEqual(p.mode("ru-dual"), "on")
        self.assertEqual(p.mode("refs"), "on")

    def test_ru_dual_promotes_ru(self) -> None:
        p = parse_track_policy(["ru=off", "ru-dual=on"])
        self.assertEqual(p.mode("ru"), "on")

    def test_enabled_ru_track_ids(self) -> None:
        p = parse_track_policy(["ru=on", "refs=off", "ru-dual=on"])
        self.assertEqual(enabled_ru_track_ids(policy=p, force_asr=False), {"ru", "ru_dual"})
        self.assertEqual(enabled_ru_track_ids(policy=p, force_asr=True), {"ru_asr", "ru_dual_asr"})

    def test_invalid_sub_raises(self) -> None:
        with self.assertRaises(RuntimeError):
            parse_track_policy(["bad=on"])
        with self.assertRaises(RuntimeError):
            parse_track_policy(["es=maybe"])
        with self.assertRaises(RuntimeError):
            parse_track_policy(["es"])


if __name__ == "__main__":
    unittest.main()
