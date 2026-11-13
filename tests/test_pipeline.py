import datetime as dt
import unittest

from social_alpha import extract
from social_alpha.evaluate import simulate
from social_alpha.market import Bars, timing
from social_alpha.symbols import SymbolIndex

SYMBOLS = [
    {"symbol": "RELIANCE", "name": "Reliance Industries", "exchange": "NSE", "kind": "stock", "universe": "nifty50"},
    {"symbol": "NSE:HFCL", "name": "Hfcl Limited", "exchange": "NSE", "kind": "stock", "universe": "nse"},
    {"symbol": "BSE:HFCL", "name": "Hfcl Limited", "exchange": "BSE", "kind": "stock", "universe": "bse"},
    {"symbol": "NSE:TBOTEK", "name": "Tbo Tek Limited", "exchange": "NSE", "kind": "stock", "universe": "nse"},
    {"symbol": "NSE:MUTHOOTFIN", "name": "Muthoot Finance Limited", "exchange": "NSE", "kind": "stock", "universe": "nse"},
    {"symbol": "NSE:BANKINDIA", "name": "Bank Of India", "exchange": "NSE", "kind": "stock", "universe": "nse"},
    {"symbol": "BSE:WORTH", "name": "Worth Investment", "exchange": "BSE", "kind": "stock", "universe": "bse"},
    {"symbol": "NSE:AGI", "name": "Agi Greenpac Limited", "exchange": "NSE", "kind": "stock", "universe": "nse"},
    {"symbol": "IDX:NIFTY50", "name": "Nifty 50", "exchange": "NSE", "kind": "index", "universe": "nse_index"},
    {"symbol": "IDX:NIFTYBANK", "name": "Nifty Bank", "exchange": "NSE", "kind": "index", "universe": "nse_index"},
]


class SymbolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ix = SymbolIndex(SYMBOLS)

    def syms(self, text, links=()):
        return [s for s, _ in self.ix.find(text, links)]

    def test_prefers_nse_listing(self):
        self.assertEqual(self.syms("$HFCL looks strong"), ["NSE:HFCL"])

    def test_company_name_and_hashtag(self):
        self.assertEqual(self.syms("6 . TBO Tek Ltd 🔖 #tbotek"), ["NSE:TBOTEK"])
        self.assertEqual(self.syms("✅Muthoot Finance Ltd added"), ["NSE:MUTHOOTFIN"])
        self.assertEqual(self.syms("#muthootfinance"), ["NSE:MUTHOOTFIN"])

    def test_bank_of_india_needs_india(self):
        self.assertEqual(self.syms("THE NATIONAL BANK OF CANADA INVESTED"), [])
        self.assertEqual(self.syms("Bank of India results"), ["NSE:BANKINDIA"])

    def test_words_and_jargon_are_not_tickers(self):
        self.assertEqual(self.syms("WORTH it. AGI soon. RSI and MACD crossing"), [])

    def test_index_aliases_and_tradingview(self):
        self.assertEqual(self.syms("#BANKNIFTY levels"), ["IDX:NIFTYBANK"])
        self.assertEqual(self.syms("chart", ["https://www.tradingview.com/chart/HFCL/abc/"]), ["NSE:HFCL"])
        self.assertEqual(self.syms("see NSE:RELIANCE"), ["RELIANCE"])


class ExtractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ix = SymbolIndex(SYMBOLS)

    def one(self, text, focused=True):
        out = extract.extract({"text": text, "links": "[]"}, self.ix, focused=focused)
        return out[0] if len(out) == 1 else out

    def test_full_call(self):
        c = self.one("HFCL buy above 112, SL 104, targets 125/140 #breakout swing")
        self.assertEqual((c["symbol"], c["kind"], c["stance"]), ("NSE:HFCL", "call", 1))
        self.assertEqual((c["entry"], c["entry_type"], c["stop"], c["targets"]), (112.0, "above", 104.0, [125.0, 140.0]))
        self.assertIn("breakout", c["setups"])
        self.assertEqual(c["horizon"], "swing")

    def test_bearish_call(self):
        c = self.one("Sell RELIANCE below 1,300 stop loss 1340 tgt 1250 head & shoulders")
        self.assertEqual((c["stance"], c["entry"], c["entry_type"], c["stop"]), (-1, 1300.0, "below", 1340.0))
        self.assertIn("head_shoulders", c["setups"])

    def test_theme_mention(self):
        c = self.one("40.Muthoot Finance Ltd 🔖 | 🔸FII Holding Increase (Sep) | ROE ~ 18.7 %", focused=True)
        self.assertEqual((c["kind"], c["stance"]), ("mention", 1))
        self.assertIn("fii_buying", c["themes"])
        self.assertIn("quality_screen", c["themes"])

    def test_multi_stock_post_drops_levels(self):
        out = extract.extract({"text": "Buy HFCL and RELIANCE, SL 100", "links": "[]"}, self.ix, focused=True)
        self.assertEqual(len(out), 2)
        self.assertTrue(all(c["kind"] == "mention" and c["stop"] is None for c in out))

    def test_unfocused_author_needs_india_context(self):
        self.assertEqual(extract.extract({"text": "HFCL breakout", "links": "[]"}, self.ix, focused=False), [])
        self.assertEqual(len(extract.extract({"text": "HFCL breakout on NSE", "links": "[]"}, self.ix, focused=False)), 1)


def _ms(day, hh, mm):
    t = dt.datetime.fromisoformat(f"{day}T{hh:02d}:{mm:02d}:00+05:30")
    return int(t.timestamp() * 1000)


class TimingTests(unittest.TestCase):
    cal = ["2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13", "2026-11-16"]

    def test_before_open(self):
        self.assertEqual(timing(_ms("2026-11-11", 8, 0), self.cal), ("2026-11-10", "2026-11-11", "open"))

    def test_during_session_fills_at_close(self):
        self.assertEqual(timing(_ms("2026-11-11", 11, 0), self.cal), ("2026-11-10", "2026-11-11", "close"))

    def test_after_close(self):
        self.assertEqual(timing(_ms("2026-11-11", 18, 0), self.cal), ("2026-11-11", "2026-11-12", "open"))

    def test_weekend(self):
        self.assertEqual(timing(_ms("2026-11-14", 12, 0), self.cal), ("2026-11-13", "2026-11-16", "open"))

    def test_future_action(self):
        self.assertEqual(timing(_ms("2026-11-16", 18, 0), self.cal), ("2026-11-16", None, "open"))


def _bars(rows, start=1):
    days = [f"2026-01-{start + i:02d}" for i in range(len(rows))]
    return Bars("X", [{"time": d, "open": o, "high": h, "low": l, "close": c, "volume": 1} for d, (o, h, l, c) in zip(days, rows)])


class SimulateTests(unittest.TestCase):
    flat = [(100, 101, 99, 100)] * 3

    def run_sim(self, rows, call, mode="open", **kw):
        b = _bars(rows)
        return simulate(b, None, call, b.dates[0], b.dates[1], mode, cost_bps=0, horizons=(2,), **kw)

    def test_target_hit(self):
        r = self.run_sim(self.flat[:1] + [(100, 104, 99, 103), (103, 111, 102, 110), (110, 110, 108, 109)],
                         {"stance": 1, "stop": 95, "targets": [110]})
        self.assertEqual((r["fill_price"], r["exit_reason"], r["exit_price"]), (100, "target", 110))
        self.assertAlmostEqual(r["trade_ret"], 0.10)
        self.assertAlmostEqual(r["r2"], 0.09)

    def test_stop_wins_same_bar(self):
        r = self.run_sim(self.flat[:1] + [(100, 112, 94, 100)] + self.flat, {"stance": 1, "stop": 95, "targets": [110]})
        self.assertEqual((r["exit_reason"], r["exit_price"]), ("stop", 95))

    def test_gap_through_stop_fills_at_open(self):
        r = self.run_sim(self.flat[:1] + [(100, 101, 99, 100), (90, 92, 88, 91)] + self.flat, {"stance": 1, "stop": 95})
        self.assertEqual((r["exit_reason"], r["exit_price"]), ("stop", 90))

    def test_buy_above_waits_for_trigger(self):
        r = self.run_sim(self.flat[:1] + [(100, 101, 99, 100), (101, 106, 100, 105), (105, 107, 104, 106)] + self.flat,
                         {"stance": 1, "entry": 104, "entry_type": "above", "stop": 97})
        self.assertEqual((r["fill_date"], r["fill_price"]), ("2026-01-03", 104))

    def test_no_fill(self):
        r = self.run_sim(self.flat * 4, {"stance": 1, "entry": 104, "entry_type": "above"})
        self.assertEqual((r["filled"], r["exit_reason"], r["resolved"]), (0, "nofill", 1))

    def test_short_call(self):
        r = self.run_sim(self.flat[:1] + [(100, 101, 99, 100), (99, 100, 89, 90)] + self.flat,
                         {"stance": -1, "stop": 105, "targets": [90]})
        self.assertEqual(r["exit_reason"], "target")
        self.assertAlmostEqual(r["trade_ret"], 0.10)

    def test_time_exit(self):
        r = self.run_sim(self.flat * 3, {"stance": 1}, max_hold=3)
        self.assertEqual((r["exit_reason"], r["exit_date"]), ("time", "2026-01-05"))

    def test_bad_levels_ignored(self):
        # stop above a long entry and a target 10x away are dropped; default 8% stop applies
        r = self.run_sim(self.flat * 3, {"stance": 1, "stop": 120, "targets": [1000]}, max_hold=2)
        self.assertEqual(r["exit_reason"], "time")

    def test_pending_when_future(self):
        b = _bars(self.flat)
        r = simulate(b, None, {"stance": 1}, b.dates[-1], None, "open")
        self.assertEqual((r["filled"], r["exit_reason"], r["resolved"]), (0, "pending", 0))


if __name__ == "__main__":
    unittest.main()
