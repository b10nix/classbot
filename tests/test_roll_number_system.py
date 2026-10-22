import unittest

from scripts.migrate_multi_major import (
    MAJORS, infer_major_from_roll, infer_major_from_archive_name,
    normalize_major, route_record,
)


class TestMultiMajorMigrationRouting(unittest.TestCase):
    def test_aliases_are_canonicalized(self):
        self.assertEqual(normalize_major("IT"), "ceit")
        self.assertEqual(normalize_major("ME"), "mech")
        self.assertEqual(normalize_major("Archi"), "archi")
        self.assertIsNone(normalize_major("unknown"))

    def test_student_major_is_inferred_only_from_explicit_roll_prefix(self):
        self.assertEqual(infer_major_from_roll("Sem-III-CEIT-01"), "ceit")
        self.assertEqual(infer_major_from_roll("Sem-2-EP-05"), "ep")
        self.assertEqual(infer_major_from_roll("01"), None)

    def test_archive_collection_names_can_identify_major(self):
        self.assertEqual(infer_major_from_archive_name("archive_funds_ceit_sem2"), "ceit")
        self.assertEqual(infer_major_from_archive_name("archive_ec_ledgers_archi_sem1"), "archi")
        self.assertIsNone(infer_major_from_archive_name("archive_funds_sem2"))

    def test_ambiguous_records_are_not_silently_assigned(self):
        destinations, doc, reason = route_record("funds", {"_id": "a", "fund_id": "sports"}, None)
        self.assertEqual(destinations, [])
        self.assertIn("major missing", reason)

    def test_explicit_legacy_major_fallback_is_opt_in(self):
        destinations, doc, reason = route_record("funds", {"_id": "a", "fund_id": "sports"}, "ceit")
        self.assertEqual(destinations, ["ceit"])
        self.assertEqual(doc["major"], "ceit")
        self.assertIsNone(reason)

    def test_all_timetable_is_copied_to_every_major(self):
        destinations, doc, reason = route_record("timetable", {"_id": "tt1", "major": "all"}, None)
        self.assertEqual(destinations, list(MAJORS))
        self.assertEqual(doc["major"], "all")
        self.assertIsNone(reason)


if __name__ == "__main__":
    unittest.main()
