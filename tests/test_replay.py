import tempfile
import unittest
from pathlib import Path

from social_alpha import db, replay, vendors
from social_alpha.config import Config
from social_alpha.market import Bars


def _cfg(tmp, min_calls=1):
    return Config(data_dir=Path(tmp), output_dir=Path(tmp), copy_min_calls=min_calls, copy_min_score=0.0)


def _call(con, uid, author, ts, symbol, signal, exit_date, x20, agree=1, themes="[]"):
    con.execute(
        """INSERT INTO posts(uid, platform, author, post_id, ts_utc, text)
           VALUES (?, 'x', ?, ?, ?, 'buy')""",
        (uid, author, uid, ts, ),
    )
    cur = con.execute(
        """INSERT INTO calls(post_uid, author, platform, ts_utc, symbol, stance, kind, themes)
           VALUES (?, ?, 'x', ?, ?, 1, 'call', ?)""",
        (uid, author, ts, symbol, themes),
    )
    con.execute(
        """INSERT INTO outcomes(call_id, signal_date, exit_date, filled, top_agree, top_best, x5, x20, x60)
           VALUES (?, ?, ?, 1, ?, 'pivot', ?, ?, ?)""",
        (cur.lastrowid, signal, exit_date, agree, x20, x20, x20),
    )
    return cur.lastrowid


class VendorTests(unittest.TestCase):
    def test_posts_and_bars_after_the_as_of_date_are_withheld(self):
        tmp = tempfile.TemporaryDirectory()
        con = db.connect(_cfg(tmp.name).db_path)
        day = "2026-01-15"
        con.execute(
            "INSERT INTO posts(uid, platform, author, post_id, ts_utc, text) VALUES ('a','x','h','1',?, 'early')",
            (vendors.published_by(day) - 1000,),
        )
        con.execute(
            "INSERT INTO posts(uid, platform, author, post_id, ts_utc, text) VALUES ('b','x','h','2',?, 'later')",
            (vendors.published_by(day) + 1000,),
        )
        visible = vendors.posts_visible(con, day)
        self.assertEqual([r["uid"] for r in visible], ["a"])
        bars = Bars("NSE:ABC", [
            {"time": "2026-01-14", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
            {"time": "2026-01-15", "open": 2, "high": 2, "low": 2, "close": 2, "volume": 1},
            {"time": "2026-01-16", "open": 3, "high": 3, "low": 3, "close": 3, "volume": 1},
        ])
        cut = vendors.bars_visible(bars, day)
        self.assertEqual(cut.dates, ["2026-01-14", "2026-01-15"])
        self.assertEqual(vendors.fundamentals_visible(day)["filings"], [])
        con.close()
        tmp.cleanup()


class ReplayTests(unittest.TestCase):
    def test_a_later_win_cannot_qualify_an_earlier_call(self):
        tmp = tempfile.TemporaryDirectory()
        cfg = _cfg(tmp.name, min_calls=1)
        con = db.connect(cfg.db_path)
        early = vendors.published_by("2024-01-02")
        late = vendors.published_by("2024-03-01")
        _call(con, "p1", "ada", early, "NSE:AAA", "2024-01-02", "2024-02-02", 0.10, agree=0)
        _call(con, "p2", "ada", late, "NSE:BBB", "2024-03-01", "2024-04-01", 0.20, agree=1)
        con.commit()
        replay.run(con, cfg)
        rows = {r["symbol"]: r for r in con.execute("SELECT * FROM desk_replay")}
        self.assertEqual(rows["NSE:AAA"]["rating"], "HOLD")
        self.assertIn("not reviewed", rows["NSE:AAA"]["reason"])
        self.assertEqual(rows["NSE:AAA"]["creator_n"], 0)
        self.assertEqual(rows["NSE:BBB"]["rating"], "BUY")
        self.assertEqual(rows["NSE:BBB"]["creator_n"], 1)
        self.assertNotIn("not reviewed", rows["NSE:BBB"]["reason"])
        scored = replay.summary(con)["by_rating"]
        self.assertEqual(scored["BUY"]["n"], 1)
        self.assertAlmostEqual(scored["BUY"]["x20_mean"], 0.20)
        con.close()
        tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
