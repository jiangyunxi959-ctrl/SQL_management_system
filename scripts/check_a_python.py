"""无需数据库的业务例子测试：预期结果直接写出来，方便初学者核对。"""

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from etl.temporal.load_reference import board_of, status_intervals
from etl.temporal.load_financials import validate
from etl.temporal.convert_member_snapshots import intervals


class BusinessRulesTest(unittest.TestCase):
    def test_membership_does_not_bridge_unobserved_days(self):
        rows = [
            dict(
                index_code="TEST",
                index_name="测试",
                stock_code="sh.600001",
                sample_date=day,
                source_url="test://daily",
            )
            for day in ["2025-05-01", "2025-05-02", "2025-05-08"]
        ]
        result = intervals(rows)
        self.assertEqual(
            [(r["start_date"], r["end_date"]) for r in result],
            [
                (date(2025, 5, 1), date(2025, 5, 3)),
                (date(2025, 5, 8), date(2025, 5, 9)),
            ],
        )
        with self.assertRaises(ValueError):
            intervals(rows + rows[:1])

    def test_status_boundary(self):
        rows = [
            (date(2025, 3, 7), "0"),
            (date(2025, 3, 10), "1"),
            (date(2025, 3, 11), "1"),
            (date(2025, 3, 12), "0"),
        ]
        self.assertEqual(
            status_intervals(rows),
            [
                ["NORMAL", date(2025, 3, 7), date(2025, 3, 10)],
                ["ST", date(2025, 3, 10), date(2025, 3, 12)],
                ["NORMAL", date(2025, 3, 12), date(2025, 3, 13)],
            ],
        )

    def test_board(self):
        self.assertEqual(board_of("sh.688001"), ("SH", "STAR"))
        self.assertEqual(board_of("sz.301001"), ("SZ", "GEM"))
        with self.assertRaises(ValueError):
            board_of("sh.600001';delete")

    def test_snapshot_date_and_missing_numbers(self):
        row = dict(
            stock_code="sh.600000",
            report_period="2024-12-31",
            announce_date="2025-04-28",
            available_date="2026-10-26",
            observed_on="2026-10-26",
            is_restated="false",
            source="TEST",
            source_url="https://example.test/report",
            source_kind="PROVIDER_SNAPSHOT",
            period_basis="ANNUAL",
            profit_basis="CONSOLIDATED",
            net_profit="0",
            roe="0.15",
        )
        result = validate(row)
        self.assertIsNone(result["revenue"])
        self.assertEqual(result["net_profit"], 0)
        self.assertEqual(str(result["roe"]), "0.15")
        row["available_date"] = "2025-04-28"
        with self.assertRaises(ValueError):
            validate(row)


if __name__ == "__main__":
    unittest.main(verbosity=2)
