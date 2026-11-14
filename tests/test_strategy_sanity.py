import unittest

from social_alpha.sanity import CHECKS, run_sanity
from top.strategies import catalog


class StrategySanityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = run_sanity()

    def test_more_than_one_thousand_strategy_checks_pass(self):
        self.assertGreaterEqual(self.report["cases"], 1000)
        self.assertEqual(self.report["failed"], 0)
        self.assertEqual(self.report["strategies"] * len(CHECKS), self.report["cases"])


def _case(strategy_id, check):
    def test(self):
        row = next(item for item in self.report["rows"] if item["id"] == strategy_id)
        self.assertNotIn(check, row["failures"], row)

    test.__name__ = f"test_{strategy_id}__{check}"
    return test


for _item in catalog():
    for _check in CHECKS:
        setattr(StrategySanityTests, f"test_{_item['id']}__{_check}", _case(_item["id"], _check))
