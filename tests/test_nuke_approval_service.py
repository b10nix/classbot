import unittest

from bson import ObjectId

from services.nuke_approval_service import (
    _fund_db_id,
    _fund_impact,
    _fund_query,
    _snapshot_digest,
    fund_snapshot,
)


class TestNukeFundDeletionHelpers(unittest.TestCase):
    def test_fund_db_id_restores_objectid(self):
        value = ObjectId()
        self.assertEqual(_fund_db_id(str(value)), value)

    def test_fund_db_id_preserves_non_objectid_string(self):
        self.assertEqual(_fund_db_id("legacy-id"), "legacy-id")

    def test_fund_query_excludes_only_internal_opening_balance(self):
        query = _fund_query("ceit", str(ObjectId()))
        self.assertIn("$and", query)
        self.assertIn({"type": {"$ne": "opening_balance"}}, query["$and"])

    def test_fund_snapshot_uses_display_name_fallback(self):
        snapshot = fund_snapshot({"_id": "abc", "fund_id": "test", "title": "Test title"})
        self.assertEqual(snapshot["fund_name"], "Test title")
        self.assertEqual(snapshot["status"], "active")

    def test_snapshot_digest_ignores_student_order(self):
        fund = {"_id": "fund-1", "fund_id": "test", "major": "ceit", "status": "active"}
        students_a = [
            {"_id": "stu-2", "paid_funds": {"test": {"amount": 500}}},
            {"_id": "stu-1", "paid_funds": {"test": 100}},
        ]
        students_b = list(reversed(students_a))
        self.assertEqual(_snapshot_digest(fund, students_a, "test"), _snapshot_digest(fund, students_b, "test"))

    def test_snapshot_digest_detects_fund_change(self):
        students = [{"_id": "stu-1", "paid_funds": {"test": 100}}]
        before = {"_id": "fund-1", "fund_id": "test", "major": "ceit", "status": "active"}
        after = {**before, "status": "cancelled"}
        self.assertNotEqual(_snapshot_digest(before, students, "test"), _snapshot_digest(after, students, "test"))

    def test_snapshot_digest_detects_paid_value_change(self):
        fund = {"_id": "fund-1", "fund_id": "test", "major": "ceit", "status": "active"}
        before = [{"_id": "stu-1", "paid_funds": {"test": 100}}]
        after = [{"_id": "stu-1", "paid_funds": {"test": 200}}]
        self.assertNotEqual(_snapshot_digest(fund, before, "test"), _snapshot_digest(fund, after, "test"))

    def test_unrelated_paid_fund_does_not_change_snapshot(self):
        fund = {"_id": "fund-1", "fund_id": "test", "major": "ceit", "status": "active"}
        before = [{"_id": "stu-1", "paid_funds": {"test": 100, "sports": 10}}]
        after = [{"_id": "stu-1", "paid_funds": {"test": 100, "sports": 900}}]
        self.assertEqual(_snapshot_digest(fund, before, "test"), _snapshot_digest(fund, after, "test"))

    def test_impact_matches_fund_id_case_insensitively_and_counts_history(self):
        students = [
            {
                "_id": "stu-1",
                "paid_funds": {"TEST": {"amount": 1200}, "sports": 500},
                "payment_history": [
                    {"fund_id": "test", "amount": 1200},
                    {"fund_id": "Sports", "amount": 500},
                ],
            },
            {
                "_id": "stu-2",
                "paid_funds": {"test": 300},
                "payment_history": [{"fund_id": "TEST", "amount": 300}],
            },
        ]
        result = _fund_impact(students, "test")
        self.assertEqual(result["paid_student_count"], 2)
        self.assertEqual(result["paid_amount_total"], 1500)
        self.assertEqual(result["payment_history_count"], 2)


if __name__ == "__main__":
    unittest.main()
