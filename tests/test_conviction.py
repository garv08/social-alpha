import unittest

from social_alpha import conviction


class FuseTests(unittest.TestCase):
    def test_full_agreement_is_a_strong_score(self):
        row = conviction.fuse(technical=1, trend=1, sentiment=1, made=100)
        self.assertEqual(row["score"], 100.0)
        self.assertEqual(row["grade"], "A")
        self.assertEqual(row["missing"], [])

    def test_missing_lenses_stay_neutral_and_named(self):
        row = conviction.fuse()
        self.assertEqual(row["score"], 50.0)
        self.assertEqual(row["grade"], "C")
        self.assertEqual(row["missing"], ["technical", "trend", "sentiment", "made"])

    def test_bear_shrinks_and_bull_cannot_pass_100(self):
        bear = conviction.fuse(technical=1, trend=1, sentiment=1, made=100, regime="bear")
        bull = conviction.fuse(technical=1, trend=1, sentiment=1, made=100, regime="bull")
        self.assertEqual(bear["score"], 85.0)
        self.assertEqual(bull["score"], 100.0)

    def test_made_batch_ranks_without_inventing_a_scale(self):
        units = conviction.made_to_unit([1.0, None, 3.0, 2.0])
        self.assertIsNone(units[1])
        self.assertGreater(units[2], units[3])
        self.assertGreater(units[3], units[0])
        self.assertAlmostEqual(max(units[0], units[2], units[3]), 100.0 * 2.5 / 3, places=6)


class BookTests(unittest.TestCase):
    def test_one_name_cannot_take_the_whole_book(self):
        rows = conviction.allocate([{"symbol": "NSE:AAA", "score": 90}])
        self.assertEqual(rows[0]["weight"], 0.25)
        self.assertEqual(rows[0]["cash"], 0.75)

    def test_four_equal_names_use_the_cap_and_no_cash(self):
        rows = conviction.allocate(
            [{"symbol": f"NSE:A{i}", "score": 60} for i in range(4)]
        )
        self.assertEqual([row["weight"] for row in rows], [0.25, 0.25, 0.25, 0.25])
        self.assertEqual(rows[0]["cash"], 0.0)

    def test_bear_keeps_a_cash_reserve(self):
        rows = conviction.allocate(
            [{"symbol": f"NSE:A{i}", "score": 60} for i in range(4)],
            regime="bear",
        )
        self.assertAlmostEqual(sum(row["weight"] for row in rows), 0.8, places=4)
        self.assertEqual(rows[0]["cash"], 0.2)
        self.assertTrue(all(row["weight"] <= 0.25 for row in rows))

    def test_vol_target_never_levers(self):
        self.assertEqual(conviction.exposure([]), 1.0)
        self.assertEqual(conviction.exposure([0.0, 0.0, 0.0]), 1.0)
        self.assertLess(conviction.exposure([0.05, -0.04, 0.06, -0.05] * 5), 1.0)
        self.assertLessEqual(conviction.exposure([0.001] * 20), 1.0)
