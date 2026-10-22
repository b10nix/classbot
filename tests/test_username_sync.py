import unittest
from types import SimpleNamespace
from unittest.mock import patch

from utils import helpers


class FakeCollection:
    def __init__(self):
        self.calls = []

    async def update_many(self, query, update):
        self.calls.append((query, update))
        return SimpleNamespace(modified_count=1)


class FakeDB:
    def __init__(self):
        self.student_info = FakeCollection()
        self.pending_posts = FakeCollection()


class TestUsernameSync(unittest.IsolatedAsyncioTestCase):
    async def test_sync_updates_student_record_and_pending_social_log_snapshot(self):
        fake_db = FakeDB()
        user = SimpleNamespace(id=12345, username="new_handle")

        with patch.object(helpers, "get_db", return_value=fake_db):
            result = await helpers.sync_user_username(user)

        self.assertTrue(result)
        self.assertEqual(len(fake_db.student_info.calls), 1)
        student_query, student_update = fake_db.student_info.calls[0]
        self.assertEqual(student_query["$or"], [{"tg_id": 12345}, {"user_id": 12345}])
        self.assertEqual(student_query["tg_username"], {"$ne": "new_handle"})
        self.assertEqual(student_update["$set"]["tg_username"], "new_handle")
        self.assertIn("username_updated_at", student_update["$set"])

        self.assertEqual(len(fake_db.pending_posts.calls), 1)
        pending_query, pending_update = fake_db.pending_posts.calls[0]
        self.assertEqual(pending_query["sender_id"], 12345)
        self.assertEqual(pending_query["sender_username"], {"$ne": "new_handle"})
        self.assertEqual(pending_update["$set"]["sender_username"], "new_handle")
        self.assertIn("sender_username_updated_at", pending_update["$set"])

    async def test_sync_accepts_username_removal(self):
        fake_db = FakeDB()
        user = SimpleNamespace(id=12345, username=None)

        with patch.object(helpers, "get_db", return_value=fake_db):
            result = await helpers.sync_user_username(user)

        self.assertTrue(result)
        self.assertIsNone(fake_db.student_info.calls[0][1]["$set"]["tg_username"])
        self.assertIsNone(fake_db.pending_posts.calls[0][1]["$set"]["sender_username"])

    async def test_update_hook_uses_effective_user(self):
        fake_db = FakeDB()
        update = SimpleNamespace(effective_user=SimpleNamespace(id=7, username="current_name"))

        with patch.object(helpers, "get_db", return_value=fake_db):
            await helpers.sync_user_username_on_update(update, None)

        self.assertEqual(fake_db.student_info.calls[0][1]["$set"]["tg_username"], "current_name")

    async def test_missing_user_is_ignored(self):
        with patch.object(helpers, "get_db") as get_db_mock:
            result = await helpers.sync_user_username(None)

        self.assertFalse(result)
        get_db_mock.assert_not_called()

    def test_profile_link_uses_current_username(self):
        user = SimpleNamespace(id=12345, first_name="Test User", username="current_handle")
        link = helpers.get_safe_user_link(user)
        self.assertIn("https://t.me/current_handle", link)
        self.assertIn("(@current_handle)", link)


if __name__ == "__main__":
    unittest.main()
