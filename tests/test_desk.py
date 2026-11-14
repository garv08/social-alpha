import json
import unittest

from social_alpha import db, desk, paper
try:
    from test_paper import DAYS, FakeMarket, PaperTests
except ImportError:  # `python -m unittest tests.test_desk` from the repo root
    from tests.test_paper import DAYS, FakeMarket, PaperTests


def packet(book="copy", technical=0, sentiment=1, just_on=False, gated=False, held=False, slots=1, technical_veto=False):
    def report(stance, text):
        return {"stance": stance, "summary": text, "just_on": just_on, "ref": "pivot", "score": 1.0}

    return {
        "book": book, "symbol": "NSE:ABC", "held": held, "slots_left": slots, "gated": gated,
        "technical_veto": technical_veto,
        "technical": report(technical, "technical"),
        "sentiment": report(sentiment, "sentiment"),
        "news": report(0, "no news feed"),
        "fundamentals": report(0, "no filings"),
    }


class SanitizeTests(unittest.TestCase):
    def test_markup_and_a_forged_buy_cannot_survive(self):
        clean = desk.sanitize_decision({
            "date": "2026-11-12", "book": "ours", "symbol": "nse:ibullsltd",
            "rating": "BUY", "action": "hold",
            "reason": "buy <script>alert(1)</script>\nnow",
            "reports_json": json.dumps({"deployment": "live", "broker": "secret", "technical": "ok"}),
        })
        self.assertEqual(clean["symbol"], "NSE:IBULLSLTD")
        self.assertEqual(clean["rating"], "HOLD")
        self.assertEqual(clean["action"], "hold")
        self.assertNotIn("<", clean["reason"])
        reports = json.loads(clean["reports_json"])
        self.assertEqual(reports["deployment"], "paper")
        self.assertNotIn("broker", reports)

    def test_bad_symbol_is_dropped(self):
        self.assertIsNone(desk.sanitize_decision(
            {"date": "2026-11-12", "book": "copy", "symbol": "not a symbol", "rating": "BUY", "action": "buy"}))

    def test_open_buy_without_a_desk_row_is_cancelled(self):
        import tempfile
        from pathlib import Path
        from social_alpha.config import Config
        tmp = tempfile.TemporaryDirectory()
        cfg = Config(data_dir=Path(tmp.name), output_dir=Path(tmp.name), desk=True)
        con = db.connect(cfg.db_path)
        con.execute("""INSERT INTO paper_orders(book, symbol, side, created, reason, status)
                       VALUES ('ours', 'NSE:IBULLSLTD', 'buy', '2026-11-12', 'pivot_break turned long', 'open')""")
        con.commit()
        cancelled = desk.sanitize_open_orders(con)
        self.assertEqual(cancelled, ["ours NSE:IBULLSLTD 2026-11-12"])
        status = con.execute("SELECT status FROM paper_orders").fetchone()[0]
        self.assertEqual(status, "cancelled")
        con.close()
        tmp.cleanup()


class DecideTests(unittest.TestCase):
    def test_copy_buy_when_social_is_bullish_and_technical_does_not_oppose(self):
        d = desk.decide(packet())
        self.assertEqual((d["rating"], d["action"]), ("BUY", "buy"))
        self.assertTrue(d["risk"]["conservative"])

    def test_opposing_technical_blocks_only_after_the_veto_is_confirmed(self):
        blocked = desk.decide(packet(technical=-1, technical_veto=True))
        self.assertEqual((blocked["rating"], blocked["action"]), ("OVERWEIGHT", "hold"))
        allowed = desk.decide(packet(technical=-1, technical_veto=False))
        self.assertEqual(allowed["action"], "buy")
        self.assertIn("not confirmed", allowed["reason"])

    def test_ours_buy_needs_a_fresh_long(self):
        self.assertEqual(desk.decide(packet(book="ours", technical=1, sentiment=0, just_on=True))["action"], "buy")
        self.assertEqual(desk.decide(packet(book="ours", technical=1, sentiment=0, just_on=False))["action"], "hold")

    def test_bearish_social_blocks_an_ours_entry(self):
        d = desk.decide(packet(book="ours", technical=1, sentiment=-1, just_on=True))
        self.assertEqual(d["action"], "hold")
        self.assertEqual(d["rating"], "OVERWEIGHT")

    def test_full_book_does_not_buy(self):
        self.assertEqual(desk.decide(packet(slots=0))["action"], "hold")

    def test_sell_is_a_note_not_an_entry(self):
        d = desk.decide(packet(technical=-1, sentiment=0, held=True))
        self.assertEqual(d["rating"], "SELL")
        self.assertEqual(d["action"], "hold")


class DeskPaperTests(PaperTests):
    def test_desk_buy_is_what_the_copy_book_sends(self):
        self.cfg.desk = True
        paper.step(self.con, self.market, self.cfg)
        self._call("x:a:new", "NSE:ABC", DAYS[1], stop=95.0)
        self.market.upto = 1
        res = paper.step(self.con, self.market, self.cfg)
        self.assertEqual(res["orders"]["copy"], ["NSE:ABC"])
        reason = self.con.execute("SELECT reason FROM paper_orders WHERE book = 'copy'").fetchone()[0]
        self.assertIn("desk BUY", reason)
        saved = json.loads(self.con.execute("SELECT reports_json FROM desk_runs").fetchone()[0])
        self.assertEqual(saved["deployment"], "paper")

    def test_unconfirmed_technical_opposition_does_not_block_the_paper_order(self):
        self.cfg.desk = True
        self.market = OpposingMarket(self.market.full)
        paper.step(self.con, self.market, self.cfg)
        self._call("x:a:new", "NSE:ABC", DAYS[1], stop=95.0)
        self.market.upto = 1
        res = paper.step(self.con, self.market, self.cfg)
        self.assertEqual(res["orders"]["copy"], ["NSE:ABC"])
        reason = self.con.execute("SELECT reason FROM paper_orders WHERE book = 'copy'").fetchone()[0]
        self.assertIn("not confirmed", reason)


class OpposingMarket(FakeMarket):
    def signals(self, bars, last_n=None):
        return bars.dates, {"bad": [0.0] * len(bars.dates)}

    def strategy_metrics(self, bars, sig, end, cost):
        return {"bad": {"score": -0.4}}
