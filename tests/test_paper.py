import tempfile
import unittest
from pathlib import Path

from social_alpha import db, paper
from social_alpha.config import Config
from social_alpha.market import Bars

DAYS = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]


class FakeMarket:
    """Serves bars up to `upto` (the simulated 'today')."""

    def __init__(self, full):
        self.full = full
        self.upto = 0

    def latest_date(self):
        return DAYS[self.upto]

    def calendar(self):
        return DAYS[: self.upto + 1]

    def bars(self, symbol):
        rows = self.full.get(symbol)
        return Bars(symbol, rows[: self.upto + 1]) if rows else None

    def signals(self, bars, last_n=None):
        return bars.dates, {}

    def strategy_metrics(self, *a, **k):
        return {}


def row(d, o, h, l, c):
    return {"time": d, "open": o, "high": h, "low": l, "close": c, "volume": 1000}


class PaperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config(data_dir=Path(self.tmp.name), output_dir=Path(self.tmp.name), paper_capital=10_000.0,
                          paper_max_positions=4, copy_min_calls=3, cost_bps=0.0, default_stop_pct=8.0, desk=False)
        self.con = db.connect(self.cfg.db_path)
        # a creator with 3 closed winning calls before the test window
        for i in range(3):
            self._call(f"x:a:{i}", "OLD", "2025-12-01", x20=0.05, exit_date="2025-12-20")
        self.market = FakeMarket({"NSE:ABC": [row(DAYS[0], 100, 101, 99, 100), row(DAYS[1], 102, 104, 101, 103),
                                              row(DAYS[2], 103, 103, 90, 92), row(DAYS[3], 92, 93, 91, 92)]})

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def _call(self, uid, symbol, signal_date, x20=None, exit_date=None, stop=None):
        cur = self.con.execute("""INSERT INTO calls(post_uid, author, platform, ts_utc, symbol, matched_by, stance, kind,
                                  setups, themes, stop, targets) VALUES (?, 'a', 'x', 0, ?, 'ticker', 1, 'call', '[]', '[]', ?, '[]')""",
                               (uid, symbol, stop))
        self.con.execute("""INSERT INTO outcomes(call_id, signal_date, filled, exit_date, x20, trade_ret, resolved)
                            VALUES (?, ?, 1, ?, ?, ?, ?)""", (cur.lastrowid, signal_date, exit_date, x20, x20, int(x20 is not None)))
        self.con.commit()

    def test_copy_book_fills_next_open_and_stops_out(self):
        paper.step(self.con, self.market, self.cfg)  # day 0: nothing to copy yet
        self._call("x:a:new", "NSE:ABC", DAYS[1], stop=95.0)  # posted after day-1 close
        self.market.upto = 1
        res = paper.step(self.con, self.market, self.cfg)
        self.assertEqual(res["orders"]["copy"], ["NSE:ABC"])
        self.market.upto = 2  # fills at 103 open, low 90 hits the 95 stop the same day
        res = paper.step(self.con, self.market, self.cfg)
        trades = [dict(r) for r in self.con.execute("SELECT * FROM paper_trades WHERE book = 'copy'")]
        self.assertEqual(len(trades), 1)
        t = trades[0]
        self.assertEqual((t["entry_date"], t["entry_price"], t["exit_reason"], t["exit_price"]), (DAYS[2], 103, "stop", 95.0))
        self.assertEqual(t["qty"], 24)  # 2,500 slot // 103
        self.assertAlmostEqual(res["books"]["copy"]["equity"], 10_000 - 24 * 8)

    def test_step_is_idempotent_per_day(self):
        paper.step(self.con, self.market, self.cfg)
        self.assertIn("already", paper.step(self.con, self.market, self.cfg)["status"])

    def test_unaffordable_share_is_skipped(self):
        self.market.full["NSE:BIG"] = [row(d, 5000, 5100, 4900, 5000) for d in DAYS]
        paper.step(self.con, self.market, self.cfg)
        self._call("x:a:big", "NSE:BIG", DAYS[1])
        self.market.upto = 1
        paper.step(self.con, self.market, self.cfg)
        self.market.upto = 2
        res = paper.step(self.con, self.market, self.cfg)
        self.assertTrue(any("NSE:BIG" in n for n in res["books"]["copy"]["notes"]))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM paper_positions").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
