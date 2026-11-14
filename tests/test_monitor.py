import tempfile
import unittest
from pathlib import Path

from social_alpha import db
from social_alpha.config import Config
from social_alpha.monitor import fetch_bars, status, top20


class MonitorTests(unittest.TestCase):
    def test_status_is_paper_and_lists_the_scan(self):
        tmp = tempfile.TemporaryDirectory()
        cfg = Config(data_dir=Path(tmp.name), output_dir=Path(tmp.name))
        con = db.connect(cfg.db_path)
        db.set_meta(con, "scan_latest", '{"date":"2026-01-02","rows":[{"symbol":"NSE:ABC","made_score":1.5}]}')
        db.set_meta(con, "market_date", "2026-01-02")
        con.commit()
        snap = status(con)
        self.assertEqual(snap["deployment"], "paper")
        self.assertEqual(top20(con)[0]["symbol"], "NSE:ABC")
        self.assertEqual(snap["market_date"], "2026-01-02")
        con.close()
        tmp.cleanup()

    def test_a_bad_symbol_is_not_looked_up(self):
        tmp = tempfile.TemporaryDirectory()
        cfg = Config(data_dir=Path(tmp.name), output_dir=Path(tmp.name), top_api="http://127.0.0.1:9")
        payload = fetch_bars(cfg, "not a symbol")
        self.assertEqual(payload["error"], "unknown symbol")
        self.assertEqual(payload["bars"], [])
        tmp.cleanup()

    def test_page_attributes_the_chart_library(self):
        page = Path("/home/dell/projects/social-alpha/social_alpha/monitor_web/index.html").read_text()
        self.assertIn("Charting by TradingView", page)
        self.assertNotIn("reverse engineer", page.lower())


if __name__ == "__main__":
    unittest.main()
