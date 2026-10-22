"""Regression tests for Major-specific class reminder and timetable safety."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from handlers.academic import academic


MM_TZ = timezone(timedelta(hours=6, minutes=30))


class FakeCursor:
    def __init__(self, records):
        self.records = records

    async def to_list(self, length=None):
        return list(self.records)


class FakeCollection:
    def __init__(self, records):
        self.records = records

    def find(self, query):
        return FakeCursor(self.records)


class FakeDatabase:
    def __init__(self, records):
        self.timetable = FakeCollection(records)


class AcademicRoutingSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_major_reminder_reaches_each_configured_major_group_once(self):
        fixed_now = datetime(2026, 10, 19, 8, 0, tzinfo=MM_TZ)  # Monday

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                if tz is None:
                    return fixed_now.replace(tzinfo=None)
                return fixed_now.astimezone(tz)

        common_record = {
            "day": "Monday",
            "major": "all",
            "periods": [{
                "time": "08:10-09:00",
                "subject": "Maths & Physics",
                "teacher": "<Mr. A>",
                "room": "A&B",
            }],
        }
        databases = [
            ("legacy", FakeDatabase([common_record])),
            ("legacy-copy", FakeDatabase([common_record])),
        ]
        configured_major_groups = [-100111, -100222, -100333]
        shared_groups = [-100901, -100902]

        bot = SimpleNamespace(send_message=AsyncMock(side_effect=lambda **kwargs: SimpleNamespace(message_id=1)))
        context = SimpleNamespace(bot=bot)

        with (
            patch.object(academic, "datetime", FixedDateTime),
            patch.object(academic, "_class_reminders_enabled", new=AsyncMock(return_value=True)),
            patch.object(academic, "get_all_major_dbs", return_value=databases),
            patch.object(academic, "get_major_group_ids", return_value=configured_major_groups),
            patch.object(academic, "schedule_academic_delete") as schedule_delete,
        ):
            await academic.class_reminder_job(context)

        sent_ids = [call.kwargs["chat_id"] for call in bot.send_message.await_args_list]
        self.assertEqual(sent_ids, configured_major_groups)
        self.assertTrue(set(sent_ids).isdisjoint(shared_groups))
        self.assertEqual(bot.send_message.await_count, len(configured_major_groups))
        sent_text = bot.send_message.await_args_list[0].kwargs["text"]
        self.assertIn("Maths &amp; Physics", sent_text)
        self.assertIn("&lt;Mr. A&gt;", sent_text)
        self.assertIn("A&amp;B", sent_text)
        self.assertEqual(schedule_delete.call_count, len(configured_major_groups))

    def test_major_mutation_targets_exclude_global_all_record(self):
        self.assertEqual(academic._get_timetable_mutation_majors("ceit"), ["ceit", "it"])
        self.assertEqual(academic._get_timetable_mutation_majors("mech"), ["mech", "me"])
        self.assertEqual(academic._get_timetable_mutation_majors("ep"), ["ep"])
        self.assertNotIn("all", academic._get_timetable_mutation_majors("ceit"))
        self.assertNotIn("all", academic._get_timetable_mutation_majors("ep"))

    def test_all_major_reminder_targets_are_unique_major_group_ids(self):
        ids = [-100111, -100222, -100111]
        with patch.object(academic, "get_major_group_ids", return_value=ids):
            target = academic._get_class_reminder_targets("all")
        self.assertEqual(target, ("ALL MAJORS", [-100111, -100222], "all"))

    def test_major_specific_task_targets_use_only_major_group_mapping(self):
        with patch.dict(academic.MAJOR_GROUP_IDS, {"ceit": [-100111]}, clear=True):
            self.assertEqual(academic.get_major_groups("ceit"), [-100111])

    async def test_non_super_admin_cannot_manage_global_timetable(self):
        reply = AsyncMock()
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=123),
            message=SimpleNamespace(reply_text=reply),
        )
        context = SimpleNamespace(bot=SimpleNamespace())

        with (
            patch.object(academic, "is_super_admin", return_value=False),
            patch.object(academic, "is_admin_check", new=AsyncMock(return_value=True)) as scoped_check,
        ):
            allowed = await academic._can_manage_timetable(update, context, "all")

        self.assertFalse(allowed)
        scoped_check.assert_not_awaited()
        reply.assert_awaited_once()

    async def test_super_admin_can_manage_global_timetable(self):
        update = SimpleNamespace(effective_user=SimpleNamespace(id=1), message=SimpleNamespace(reply_text=AsyncMock()))
        context = SimpleNamespace(bot=SimpleNamespace())
        with patch.object(academic, "is_super_admin", return_value=True):
            allowed = await academic._can_manage_timetable(update, context, "all")
        self.assertTrue(allowed)

    def test_html_text_escapes_markup_delimiters(self):
        self.assertEqual(academic._html_text("A&B <C>"), "A&amp;B &lt;C&gt;")
        self.assertEqual(academic._html_text(None, "Unknown"), "Unknown")


if __name__ == "__main__":
    unittest.main()
