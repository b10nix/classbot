import unittest

from services.fund_selection_service import build_undo_options
from services.finance_service import build_fund_major_filter, build_major_filter


class TestUndoFundOptions(unittest.TestCase):
    def test_only_latest_payment_per_fund_is_offered(self):
        student = {
            "paid_funds": {"sports": {"amount": 200}, "mc": {"amount": 0}},
            "payment_history": [
                {"fund_id": "sports", "amount": 100, "date": "2026-10-01", "transaction_id": "old"},
                {"fund_id": "sports", "amount": 100, "date": "2026-10-05", "transaction_id": "new"},
                {"fund_id": "mc", "amount": 50, "date": "2026-10-02", "transaction_id": "reversed"},
            ],
        }
        options = build_undo_options(student, {"sports": "Sports Fund"})
        self.assertEqual(len(options), 1)
        self.assertEqual(options[0]["fund_id"], "sports")
        self.assertEqual(options[0]["transaction_id"], "new")
        self.assertEqual(options[0]["fund_name"], "Sports Fund")
        self.assertEqual(len(options[0]["transaction_fingerprint"]), 64)

    def test_no_option_without_current_paid_amount_or_history(self):
        student = {
            "paid_funds": {"mc": {"amount": 0}, "extra": {"amount": 20}},
            "payment_history": [{"fund_id": "mc", "amount": 10, "date": "2026-10-01"}],
        }
        self.assertEqual(build_undo_options(student), [])

    def test_fund_ids_are_normalized_for_legacy_case_variants(self):
        student = {
            "paid_funds": {"SPORTS": {"amount": 100}},
            "payment_history": [{"fund_id": "Sports", "amount": 100, "date": "2026-10-05"}],
        }
        options = build_undo_options(student)
        self.assertEqual(options[0]["fund_id"], "sports")


    def test_payment_fingerprint_changes_when_legacy_entry_changes(self):
        from services.finance_service import payment_entry_fingerprint
        before = {"fund_id": "sports", "amount": 100, "date": "2026-10-05"}
        after = {"fund_id": "sports", "amount": 100, "date": "2026-10-05", "received_by": "EC B"}
        self.assertNotEqual(payment_entry_fingerprint(before), payment_entry_fingerprint(after))


class TestStrictFundMajorPartition(unittest.TestCase):
    def test_fund_filter_requires_explicit_major(self):
        query = build_fund_major_filter("ceit")
        self.assertEqual(
            query,
            {"major": {"$in": ["it", "IT", "It", "ceit", "CEIT", "Ceit"]}},
        )
        self.assertNotIn("$or", query)
        self.assertNotIn("$exists", str(query))

    def test_unassigned_legacy_fund_is_not_silently_ceit(self):
        fund_filter = build_fund_major_filter("ceit")
        legacy_ceit_compatibility_filter = build_major_filter("ceit")
        self.assertNotIn({"major": {"$exists": False}}, [fund_filter])
        # General student compatibility may still include untagged CEIT legacy
        # rows; fund documents intentionally must not.
        self.assertIn("$or", legacy_ceit_compatibility_filter)
        self.assertNotIn("$or", fund_filter)

    def test_each_major_has_a_distinct_strict_fund_filter(self):
        self.assertEqual(
            build_fund_major_filter("ep"),
            {"major": {"$in": ["ep", "EP", "Ep"]}},
        )
        self.assertNotEqual(build_fund_major_filter("ceit"), build_fund_major_filter("ep"))

    def test_invalid_major_fails_closed(self):
        with self.assertRaises(ValueError):
            build_fund_major_filter("unknown")


if __name__ == "__main__":
    unittest.main()
