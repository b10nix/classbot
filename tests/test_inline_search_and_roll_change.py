import unittest

from handlers.student.student_data import parse_inline_search_query


class TestSuperAdminInlineSearch(unittest.TestCase):
    def test_super_admin_numeric_roll_searches_all_majors(self):
        plan = parse_inline_search_query("10", "ceit", True)
        self.assertIsNone(plan["error"])
        self.assertIsNone(plan["target_major"])
        self.assertEqual(plan["roll_number"], 10)

    def test_super_admin_major_prefix_scopes_search(self):
        self.assertEqual(parse_inline_search_query(".mc 10", "ceit", True)["target_major"], "mc")
        self.assertEqual(parse_inline_search_query(".me 10", "ceit", True)["target_major"], "mech")
        self.assertEqual(parse_inline_search_query(".archi 10", "ceit", True)["target_major"], "archi")

    def test_super_admin_name_search_is_global_without_prefix(self):
        plan = parse_inline_search_query("Aung Phyo Kyaw", "ceit", True)
        self.assertIsNone(plan["error"])
        self.assertIsNone(plan["target_major"])
        self.assertIsNone(plan["roll_number"])

    def test_full_roll_number_scopes_super_admin_search_to_embedded_major(self):
        plan = parse_inline_search_query("Sem-III-CEIT-10", "mc", True)
        self.assertIsNone(plan["error"])
        self.assertEqual(plan["target_major"], "ceit")
        self.assertEqual(plan["roll_number"], 10)

    def test_prefix_conflicting_with_full_roll_is_rejected(self):
        plan = parse_inline_search_query(".mc Sem-III-CEIT-10", "ceit", True)
        self.assertEqual(plan["error"], "major_mismatch")


class TestECInlineSearchMajorIsolation(unittest.TestCase):
    def test_unprefixed_query_is_scoped_to_assigned_major(self):
        plan = parse_inline_search_query("10", "ceit", False)
        self.assertIsNone(plan["error"])
        self.assertEqual(plan["target_major"], "ceit")

    def test_same_major_prefix_is_allowed(self):
        plan = parse_inline_search_query(".ceit 10", "ceit", False)
        self.assertIsNone(plan["error"])
        self.assertEqual(plan["target_major"], "ceit")

    def test_cross_major_prefix_is_denied(self):
        plan = parse_inline_search_query(".mc 10", "ceit", False)
        self.assertEqual(plan["error"], "denied")


if __name__ == "__main__":
    unittest.main()
