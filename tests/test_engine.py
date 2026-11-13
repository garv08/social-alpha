import json
import tempfile
import unittest
from pathlib import Path

from social_alpha import db, engine, paper
from social_alpha.config import Config


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config(data_dir=Path(self.tmp.name), output_dir=Path(self.tmp.name), paper_capital=10_000.0)
        self.con = db.connect(self.cfg.db_path)

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def _call(self, author, day, x20, setups=None, top_agree=None, symbol="NSE:ABC"):
        cur = self.con.execute(
            """INSERT INTO calls(post_uid, author, platform, ts_utc, symbol, matched_by, stance, kind, setups, themes)
               VALUES (?, ?, 'x', 0, ?, 'ticker', 1, 'call', ?, '[]')""",
            (f"{author}:{day}:{x20}:{setups}:{top_agree}:{cur_id(self)}", author, symbol, json.dumps(setups or [])))
        self.con.execute(
            """INSERT INTO outcomes(call_id, signal_date, filled, x20, trade_ret, top_agree, resolved)
               VALUES (?, ?, 1, ?, ?, ?, 1)""",
            (cur.lastrowid, day, x20, x20, top_agree))

    def test_blocks_author_only_when_loss_repeats_out_of_sample(self):
        for i, day in enumerate(_days("2024-01", 12)):
            self._call("loser", day, -0.02)
            self._call("winner", day, 0.03)
        for i, day in enumerate(_days("2024-06", 12)):
            self._call("loser", day, -0.01)
            self._call("winner", day, 0.02)
        self.con.commit()
        res = engine.run(self.con, self.cfg, "2024-09-01")
        self.assertEqual(res["status"], "validated")
        self.assertIn("loser", res["gates"]["blocked_authors"])
        self.assertNotIn("winner", res["gates"]["blocked_authors"])
        self.assertEqual(res["live"]["status"], "refused")
        self.assertFalse(engine.load_gates(self.con)["live_trading"])

    def test_does_not_block_on_an_early_loss_that_later_recovers(self):
        for day in _days("2024-01", 12):
            self._call("mixed", day, -0.03)
        for day in _days("2024-06", 12):
            self._call("mixed", day, 0.04)
        self.con.commit()
        res = engine.run(self.con, self.cfg, "2024-09-01")
        self.assertNotIn("mixed", res["gates"]["blocked_authors"])
        rejected = [p for p in res["proposals"] if p["kind"] == "block_author" and p["target"] == "mixed"]
        self.assertTrue(rejected)
        self.assertEqual(rejected[0]["status"], "rejected")

    def test_leaves_gates_alone_when_the_unseen_window_is_too_small(self):
        engine.save_gates(self.con, {"blocked_authors": ["kept"], "blocked_setups": [], "require_top_agree": []})
        for day in _days("2024-01", 3):
            self._call("thin", day, -0.05)
        self.con.commit()
        res = engine.run(self.con, self.cfg, "2024-02-01")
        self.assertEqual(res["status"], "insufficient")
        self.assertEqual(engine.load_gates(self.con)["blocked_authors"], ["kept"])

    def test_blocks_a_setup_that_fails_on_both_sides(self):
        for day in _days("2024-01", 12):
            self._call("a", day, -0.02, setups=["breakout"])
            self._call("b", day, 0.02, setups=["support"])
        for day in _days("2024-06", 12):
            self._call("a", day, -0.01, setups=["breakout"])
            self._call("b", day, 0.01, setups=["support"])
        self.con.commit()
        res = engine.run(self.con, self.cfg, "2024-09-01")
        self.assertIn("breakout", res["gates"]["blocked_setups"])
        self.assertNotIn("support", res["gates"]["blocked_setups"])

    def test_requires_top_agreement_only_when_disagreement_loses_out_of_sample(self):
        for day in _days("2024-01", 12):
            self._call("crowd", day, 0.03, top_agree=1)
            self._call("crowd", day, -0.03, top_agree=0)
        for day in _days("2024-06", 12):
            self._call("crowd", day, 0.02, top_agree=1)
            self._call("crowd", day, -0.02, top_agree=0)
        self.con.commit()
        res = engine.run(self.con, self.cfg, "2024-09-01")
        self.assertIn("crowd", res["gates"]["require_top_agree"])

    def test_stored_live_flag_cannot_stick(self):
        self.con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('engine_gates', ?)",
                         (json.dumps({"blocked_authors": [], "live_trading": True}),))
        self.con.commit()
        self.assertFalse(engine.load_gates(self.con)["live_trading"])
        self.assertEqual(engine.request_live(self.con)["status"], "refused")

    def test_copy_book_skips_a_blocked_author(self):
        engine.save_gates(self.con, {"blocked_authors": ["a"], "blocked_setups": [], "require_top_agree": []})
        self.assertIn("blocked", engine.skip_reason(engine.load_gates(self.con), "a", "[]", 1))
        self.assertIsNone(engine.skip_reason(engine.load_gates(self.con), "other", "[]", None))
        self.assertIn("setup", engine.skip_reason({"blocked_authors": [], "blocked_setups": ["breakout"], "require_top_agree": []},
                                                  "other", '["breakout"]', 1))


def cur_id(test):
    cur_id.n += 1
    return cur_id.n


cur_id.n = 0


def _days(prefix, n):
    return [f"{prefix}-{i + 1:02d}" for i in range(n)]


class PaperGateTests(unittest.TestCase):
    def test_skip_reason_is_what_paper_consults(self):
        # paper imports engine.skip_reason; this pins the contract the copy book uses
        self.assertTrue(callable(paper.engine.skip_reason))
