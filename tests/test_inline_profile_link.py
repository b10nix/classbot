"""Regression tests for inline-search View Profile link selection.

These tests extract the pure message builder so they can run without Telegram
or MongoDB installed.
"""
import ast
import html
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT_ROOT / "handlers" / "student" / "student_data.py"


def load_build_student_message():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "build_student_message"
    )
    module = ast.Module(body=[function], type_ignores=[])
    namespace = {
        "html": html,
        "get_short_roll": lambda student: student.get("roll_no", ""),
        "extract_major_from_roll": lambda roll: "ceit" if "ceit" in roll.lower() else None,
        "build_fund_text": lambda paid, funds: "No active funds",
    }
    exec(compile(module, str(SOURCE), "exec"), namespace)
    return namespace["build_student_message"]


class TestInlineSearchProfileLink(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build_message = staticmethod(load_build_student_message())

    def build(self, username=None, user_id=None):
        student = {
            "name": "Test Student", "roll_no": "10", "major": "ceit",
            "tg_username": username, "user_id": user_id,
        }
        return self.build_message(student, [])

    def test_username_link_is_preferred_over_numeric_id_link(self):
        message = self.build(username="current_handle", user_id=12345)
        self.assertIn("https://t.me/current_handle", message)
        self.assertIn("View Profile (@current_handle)", message)
        self.assertNotIn("tg://user?id=12345", message)

    def test_username_with_at_prefix_is_normalized(self):
        message = self.build(username="@current_handle", user_id=12345)
        self.assertIn("https://t.me/current_handle", message)
        self.assertNotIn("https://t.me/@current_handle", message)

    def test_numeric_id_is_fallback_when_username_missing(self):
        message = self.build(username=None, user_id=12345)
        self.assertIn("tg://user?id=12345", message)

    def test_unlinked_student_shows_not_linked(self):
        message = self.build(username=None, user_id=None)
        self.assertIn("Not Linked", message)


if __name__ == "__main__":
    unittest.main()
