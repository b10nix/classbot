# handlers/academic/academic.py

import os
import logging
import asyncio
import html
import random
from datetime import datetime, timedelta, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode

from database.db import get_db, get_all_major_dbs, get_shared_db
from utils.constants import (
    DAYS_OF_WEEK, SUBJECTS_LIST, MAJOR_GROUP_IDS,
    get_default_majors_for_group, get_major_group_ids,
)
from utils.auth import is_super_admin
from services.major_service import (
    normalize_major,
    get_user_major,
    is_major_authorized,
    resolve_user_major_from_groups,
    find_student_record_by_user_id
)
from services.finance_service import build_major_filter

logger = logging.getLogger(__name__)
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
T_SUBJECT, T_DATE, T_TIME, T_LEAD_HOURS, T_CONTENT = range(70, 75)
TASK_USER_DATA_KEYS = {
    "t_edit_id", "t_type", "t_major", "t_reminder_lead_hours", "t_date_str",
    "t_target_time", "t_target_dt", "t_subject", "t_subject_map",
}


def _clear_task_user_data(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear only Task/Tutorial wizard values, leaving unrelated conversation state intact."""
    for key in TASK_USER_DATA_KEYS:
        context.user_data.pop(key, None)


def _get_timetable_query_majors(major: str) -> list[str]:
    """Map a Major to its stored tags plus common `all` records for timetable views."""
    norm = normalize_major(major) or major.lower()
    if norm == "ceit":
        return ["ceit", "it", "all"]
    if norm == "mech":
        return ["mech", "me", "all"]
    return [norm, "all"]


def _get_timetable_mutation_majors(major: str) -> list[str]:
    """Map a Major only to its own stored tags; never include global `all` records."""
    norm = normalize_major(major) or str(major).strip().lower()
    if norm == "ceit":
        return ["ceit", "it"]
    if norm == "mech":
        return ["mech", "me"]
    return [norm]


async def is_admin_check(update: Update, context: ContextTypes.DEFAULT_TYPE, major: str | None = None) -> bool:
    user_id = update.effective_user.id
    if await is_major_authorized(user_id, major):
        return True
    if update.effective_chat.type in ['group', 'supergroup']:
        try:
            admins = await context.bot.get_chat_administrators(update.effective_chat.id)
            admin_ids = [admin.user.id for admin in admins]
            if user_id in admin_ids:
                return True
        except Exception:
            pass
    return False


def _html_text(value, default: str = "") -> str:
    """Escape dynamic values before inserting them into Telegram HTML messages."""
    if value is None:
        value = default
    return html.escape(str(value), quote=False)


def get_major_groups(major: str) -> list[int]:
    """Return only the configured private group(s) for a canonical Major."""
    canonical = normalize_major(major) or str(major).strip().lower()
    # MAJOR_GROUP_IDS is constructed in utils.constants after shared group IDs
    # are excluded, so this function never targets CLASS_GROUP_ID destinations.
    return list(MAJOR_GROUP_IDS.get(canonical, []))


def _get_class_reminder_targets(raw_major: str, db_major: str | None = None) -> tuple[str, list[int], str] | None:
    """Resolve class reminder destinations without ever using shared CLASS_GROUP_ID groups.

    Returns (message label, target group IDs, dedupe major key). For `all`, each
    unique, explicitly configured Major GP receives one copy; shared groups are
    excluded by MAJOR_GROUP_IDS construction in utils.constants.
    """
    raw = str(raw_major or "").strip().lower()
    if raw == "all":
        # get_major_group_ids() contains only Major-specific groups; shared
        # CLASS_GROUP_ID destinations are filtered out in utils.constants.
        return "ALL MAJORS", list(dict.fromkeys(get_major_group_ids())), "all"

    major = normalize_major(raw) or normalize_major(db_major)
    if not major:
        return None
    return major.upper(), get_major_groups(major), major


async def _can_manage_timetable(update: Update, context: ContextTypes.DEFAULT_TYPE, major: str) -> bool:
    """Only Super Admins may use global ('all') timetable commands."""
    if major == "all":
        if is_super_admin(update.effective_user.id):
            return True
        await update.message.reply_text("⛔ Major အားလုံးအတွက် timetable ကို Super Admin များသာ ပြင်ဆင်/ဖျက်နိုင်ပါသည်။")
        return False
    return await is_admin_check(update, context, major=major)


async def _resolve_academic_major(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str | None:
    """Resolve Major from explicit Super Admin input, student/EC records, or Major GP membership."""
    user_id = update.effective_user.id
    if context.args and is_super_admin(user_id):
        explicit = normalize_major(context.args[0])
        if explicit:
            return explicit

    student, student_major = await find_student_record_by_user_id(user_id)
    if student and student_major:
        return student_major

    chat = update.effective_chat
    if chat and chat.type in ("group", "supergroup"):
        configured = get_default_majors_for_group(chat.id)
        if len(configured) == 1:
            return next(iter(configured))

    group_major, _ = await resolve_user_major_from_groups(user_id, context.bot)
    if group_major:
        return group_major
    return await get_user_major(user_id)


# ================= 🗑 Auto Delete Helpers =================
background_tasks = set()


async def delayed_delete_task(bot, chat_id, msg_ids, delay):
    await asyncio.sleep(delay)
    for msg_id in msg_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass


def schedule_academic_delete(context, chat_id, msg_ids, delay=60):
    task = asyncio.create_task(delayed_delete_task(context.bot, chat_id, msg_ids, delay))
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)


# ================= 🔔 ၁၀ မိနစ်ကြိုတင် သတိပေးစနစ် (ROUTING SYSTEM) =================
async def _class_reminders_enabled() -> bool:
    shared = get_shared_db()
    if shared is None:
        return False
    setting = await shared.settings.find_one({"type": "auto_class_reminders"})
    return bool(setting.get("enabled", True)) if setting else True


async def class_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    """Send 10-minute class reminders to configured Major GP(s) only."""
    if not await _class_reminders_enabled():
        return

    databases = get_all_major_dbs()
    if not databases:
        return

    mm_tz = timezone(timedelta(hours=6, minutes=30))
    now = datetime.now(mm_tz)
    if now.weekday() > 4:
        return
    current_day = DAYS_OF_WEEK[now.weekday()]
    target_time_str = (now + timedelta(minutes=10)).strftime("%H:%M")
    sent_reminders = set()

    for db_major, database in databases:
        if database is None:
            continue
        records = await database.timetable.find({"day": current_day}).to_list(length=None)
        for record in records:
            raw_record_major = str(record.get("major", "")).strip().lower()
            destination = _get_class_reminder_targets(raw_record_major, db_major)
            if destination is None:
                logger.debug("Skipping class reminder with unknown Major: %r", raw_record_major)
                continue

            major_label, groups, dedupe_major = destination
            if not groups:
                if raw_record_major == "all":
                    logger.debug("No Major GP configured; ALL-major class reminder not sent.")
                else:
                    logger.debug("No Major GP configured for %s; class reminder not sent.", major_label)
                continue

            for period in record.get("periods", []):
                start_time = str(period.get("time", "")).split("-")[0].strip()
                if start_time != target_time_str:
                    continue

                subject = str(period.get("subject") or "Unknown")
                teacher = str(period.get("teacher") or "Unknown")
                room = str(period.get("room") or "Unknown")
                dedupe_key = (dedupe_major, current_day, start_time, subject, teacher, room)
                if dedupe_key in sent_reminders:
                    continue
                sent_reminders.add(dedupe_key)

                msg = (
                    f"🔔 <b>[{major_label}] အတန်းစရန် (၁၀) မိနစ်သာ လိုပါတော့သည်!</b>\n\n"
                    f"📚 <b>ဘာသာရပ်:</b> {_html_text(subject)}\n"
                    f"👨‍🏫 <b>ဆရာ/မ:</b> {_html_text(teacher)}\n"
                    f"🏫 <b>အခန်း:</b> {_html_text(room)}"
                )
                for gid in groups:
                    try:
                        sent_msg = await context.bot.send_message(
                            chat_id=gid, text=msg, parse_mode=ParseMode.HTML
                        )
                        schedule_academic_delete(context, gid, [sent_msg.message_id], 600)
                    except Exception:
                        logger.exception("Failed sending %s class reminder to Major group %s", major_label, gid)


async def autott_off_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Disable the automatic 10-minute class reminders. Super Admin only."""
    if not is_super_admin(update.effective_user.id):
        return await update.message.reply_text("⛔ ဤ command ကို Super Admin များသာ သုံးနိုင်သည်။")
    db = get_shared_db()
    if db is None:
        return await update.message.reply_text("❌ Database ချိတ်ဆက်မှု မအောင်မြင်ပါ။")
    mm_tz = timezone(timedelta(hours=6, minutes=30))
    await db.settings.update_one(
        {"type": "auto_class_reminders"},
        {"$set": {"enabled": False, "updated_by": update.effective_user.id, "updated_at": datetime.now(mm_tz)}},
        upsert=True,
    )
    await update.message.reply_text("🔕 အတန်းစမည့်အချိန် ၁၀ မိနစ်ကြိုတင် Auto Reminder ကို ပိတ်လိုက်ပါပြီ။\nပြန်ဖွင့်ရန် /autott_on")


async def autott_on_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Enable the automatic 10-minute class reminders. Super Admin only."""
    if not is_super_admin(update.effective_user.id):
        return await update.message.reply_text("⛔ ဤ command ကို Super Admin များသာ သုံးနိုင်သည်။")
    db = get_shared_db()
    if db is None:
        return await update.message.reply_text("❌ Database ချိတ်ဆက်မှု မအောင်မြင်ပါ။")
    mm_tz = timezone(timedelta(hours=6, minutes=30))
    await db.settings.update_one(
        {"type": "auto_class_reminders"},
        {"$set": {"enabled": True, "updated_by": update.effective_user.id, "updated_at": datetime.now(mm_tz)}},
        upsert=True,
    )
    await update.message.reply_text("🔔 အတန်းစမည့်အချိန် ၁၀ မိနစ်ကြိုတင် Auto Reminder ကို ပြန်ဖွင့်လိုက်ပါပြီ။\nပိတ်ရန် /autott_off")


async def autott_status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the automatic class reminder status. Super Admin only."""
    if not is_super_admin(update.effective_user.id):
        return await update.message.reply_text("⛔ ဤ command ကို Super Admin များသာ သုံးနိုင်သည်။")
    enabled = await _class_reminders_enabled()
    status = "ဖွင့်ထားသည် ✅" if enabled else "ပိတ်ထားသည် 🔕"
    await update.message.reply_text(f"📣 Class Auto Reminder: {status}\nဖွင့်ရန် /autott_on | ပိတ်ရန် /autott_off")


# ================= 📅 TIMETABLE VIEW =================
async def build_timetable_message(major: str, day_idx: int):
    day_name = DAYS_OF_WEEK[day_idx]
    canon_major = normalize_major(major) or major.lower()
    db = get_db(major=canon_major)

    text = f"📅 <b>{canon_major.upper()} TIMETABLE - {day_name}</b>\n━━━━━━━━━━━━━━━━━━\n"
    has_class = False

    if db is not None:
        cursor = db.timetable.find({
            "day": day_name,
            "major": {"$in": _get_timetable_query_majors(canon_major)}
        })
        records = await cursor.to_list(length=None)

        all_periods = []
        for record in records:
            if "periods" in record:
                all_periods.extend(record["periods"])

        all_periods.sort(key=lambda x: x["time"])

        for p in all_periods:
            has_class = True
            text += f"⏰ <b>{_html_text(p.get('time', 'Unknown'))}</b>\n"
            text += f"📚 Subject: {_html_text(p.get('subject', 'Unknown'))}\n"
            text += f"👨‍🏫 Teacher: {_html_text(p.get('teacher', 'Unknown'))}\n"
            text += f"🏫 Room: {_html_text(p.get('room', 'Unknown'))}\n\n"

    if not has_class:
        text += "🎉 <i>ဒီနေ့အတွက် အတန်းမရှိပါ။ (Free Day)</i>\n"

    prev_idx = (day_idx - 1) % 5
    next_idx = (day_idx + 1) % 5
    keyboard = [[
        InlineKeyboardButton("⬅️", callback_data=f"tt_{canon_major}_{prev_idx}"),
        InlineKeyboardButton(day_name[:3], callback_data="ignore"),
        InlineKeyboardButton("➡️", callback_data=f"tt_{canon_major}_{next_idx}")
    ]]
    return text, InlineKeyboardMarkup(keyboard)


async def get_timetable_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_msg_id = update.message.message_id

    cmd = update.message.text.split()[0].lower().replace('/', '').split('@')[0]
    if cmd in ("timetable", "tt"):
        major = await _resolve_academic_major(update, context)
    else:
        major = normalize_major(cmd) or cmd

    if not major:
        return await update.message.reply_text("⚠️ Major ကို အတည်ပြု၍ မရပါ။ Major Group မှ /timetable ကို ပြန်စမ်းပါ။")

    mm_tz = timezone(timedelta(hours=6, minutes=30))
    now = datetime.now(mm_tz)
    day_idx = now.weekday()

    if now.hour >= 16:
        day_idx += 1
    if day_idx > 4:
        day_idx = 0

    text, markup = await build_timetable_message(major, day_idx)
    sent = await update.message.reply_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
    schedule_academic_delete(context, chat_id, [user_msg_id, sent.message_id], 60)


async def change_timetable_day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.data == "ignore":
        return await query.answer()

    _, major, day_idx_str = query.data.split('_')
    day_idx = int(day_idx_str)

    text, markup = await build_timetable_message(major, day_idx)
    try:
        await query.edit_message_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
    except Exception:
        pass
    await query.answer()


# ================= 📝 TASKS & TUTORIALS VIEW (AUTO-DETECTS MAJOR) =================
async def show_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_msg_id = update.message.message_id
    cmd = update.message.text.split()[0].lower()
    task_type = "tutorial" if "tutorial" in cmd else "task"

    major = await _resolve_academic_major(update, context)
    if not major:
        return await update.message.reply_text("⚠️ သင်၏ Major ကို အတည်ပြု၍ မရပါ။ သက်ဆိုင်ရာ Major Group မှ ပြန်စမ်းပါ။")
    db = get_db(major=major)
    if db is None:
        return await update.message.reply_text("❌ Database ချိတ်ဆက်မှု မအောင်မြင်ပါ။")

    mm_tz = timezone(timedelta(hours=6, minutes=30))
    now = datetime.now(mm_tz)

    query = {
        "$and": [
            build_major_filter(major),
            {"type": task_type, "target_dt": {"$gte": now}}
        ]
    }
    cursor = db.tasks.find(query).sort("target_dt", 1)
    tasks = await cursor.to_list(length=100)

    title = (
        f"📚 <b>[{major.upper()}] TUTORIALS & EXAMS</b>"
        if task_type == "tutorial"
        else f"📝 <b>[{major.upper()}] ASSIGNMENTS & TASKS</b>"
    )

    if not tasks:
        msg = f"{title}\n━━━━━━━━━━━━━━━━━━\n🎉 <b>ဘာမှမရှိသေးဘူး။ ဘာလဲ စာလုပ်ချင်လို့လား။</b>"
        sent = await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
        schedule_academic_delete(context, chat_id, [user_msg_id, sent.message_id], 60)
        return

    grouped = {}
    for t in tasks:
        d_str = t.get('date_str', 'Unknown Date')
        grouped.setdefault(d_str, []).append(t)

    msg = f"{title}\n━━━━━━━━━━━━━━━━━━\n"
    for d_str, items in grouped.items():
        msg += f"📅 <b>{_html_text(d_str, 'Unknown Date')}</b>\n"
        for t in items:
            target_dt = t.get("target_dt")
            if target_dt:
                if target_dt.tzinfo is None:
                    target_dt = target_dt.replace(tzinfo=timezone.utc)
                time_label = target_dt.astimezone(mm_tz).strftime("%I:%M %p")
            else:
                time_label = t.get("time_str", "Time unknown")
            lead_hours = int(t.get("reminder_lead_hours", 72 if task_type == "tutorial" else 18))
            msg += f"  🔹 <code>[ID:{_html_text(t.get('short_id', ''))}]</code> <b>{_html_text(t.get('subject', 'Unknown'))}</b> — <code>{_html_text(time_label)}</code>\n"
            msg += f"      ⏰ ကြိုတင်သတိပေး: {lead_hours} နာရီ\n"
            msg += f"      👉 <i>{_html_text(t.get('content', ''))}</i>\n"
        msg += "\n"

    sent = await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
    schedule_academic_delete(context, chat_id, [user_msg_id, sent.message_id], 120)


# ================= 📝 TASKS & TUTORIALS (ADD & EDIT) =================
async def _get_subjects_for_major(major: str) -> list[str]:
    """Combines standard subjects with subjects dynamically extracted from the major's timetable."""
    db = get_db(major=major)
    dynamic_subjects = list(SUBJECTS_LIST)
    # Replace hardcoded 'CEIT' with the active major's upper code if different
    if major.upper() not in dynamic_subjects:
        dynamic_subjects.append(major.upper())

    if db is not None:
        tt_majors = _get_timetable_query_majors(major)
        timetables = await db.timetable.find({"major": {"$in": tt_majors}}).to_list(length=None)
        for tt in timetables:
            for p in tt.get("periods", []):
                subj = p.get("subject", "").strip()
                if (
                    subj
                    and "self study" not in subj.lower()
                    and "lunch" not in subj.lower()
                    and subj not in dynamic_subjects
                ):
                    dynamic_subjects.append(subj)
    return dynamic_subjects


def _task_cancel_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="task_cancel")]])


async def t_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel the current task/tutorial wizard using an inline button or /cancel."""
    query = update.callback_query
    if query:
        await query.answer()
        _clear_task_user_data(context)
        try:
            await query.edit_message_text("🚫 Task/Tutorial ထည့်သွင်းခြင်းကို ပယ်ဖျက်လိုက်ပါပြီ။")
        except Exception:
            pass
    elif update.message:
        _clear_task_user_data(context)
        await update.message.reply_text("🚫 Task/Tutorial ထည့်သွင်းခြင်းကို ပယ်ဖျက်လိုက်ပါပြီ။")
    return ConversationHandler.END


async def add_task_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    major = await _resolve_academic_major(update, context)
    if not major:
        await update.message.reply_text("⚠️ Major ကို အတည်ပြု၍ မရပါ။ သက်ဆိုင်ရာ Major GP မှာ command သုံးပါ သို့မဟုတ် EC role ကို စစ်ဆေးပါ။")
        return ConversationHandler.END
    if not await is_major_authorized(user_id, major):
        await update.message.reply_text("⛔ သက်ဆိုင်ရာ Major EC သို့မဟုတ် Super Admin များသာ Task/Tutorial ထည့်သွင်းနိုင်ပါသည်။")
        return ConversationHandler.END

    cmd = update.message.text.split()[0].lower()
    db = get_db(major=major)
    if db is None:
        await update.message.reply_text("❌ Database ချိတ်ဆက်မှု မအောင်မြင်ပါ။")
        return ConversationHandler.END

    if "edit" in cmd:
        if len(context.args) < 1:
            await update.message.reply_text(f"⚠️ အသုံးပြုနည်း: <code>{cmd} &lt;ID&gt;</code>", parse_mode=ParseMode.HTML)
            return ConversationHandler.END
        try:
            task_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("❌ ID သည် ဂဏန်းဖြစ်ရမည်။")
            return ConversationHandler.END

        task = await db.tasks.find_one({"$and": [build_major_filter(major), {"short_id": task_id}]})
        if not task:
            await update.message.reply_text(f"❌ <b>[{major.upper()}]</b> တွင် ထို ID ဖြင့် ရှာမတွေ့ပါ။", parse_mode=ParseMode.HTML)
            return ConversationHandler.END

        context.user_data['t_edit_id'] = task_id
        context.user_data['t_type'] = task.get("type", "task")
        context.user_data['t_major'] = task.get("major") or major
        context.user_data['t_reminder_lead_hours'] = int(task.get("reminder_lead_hours", 72 if task.get("type") == "tutorial" else 18))
        old_target = task.get("target_dt")
        if old_target:
            if old_target.tzinfo is None:
                old_target = old_target.replace(tzinfo=timezone.utc)
            old_target = old_target.astimezone(timezone(timedelta(hours=6, minutes=30)))
            context.user_data['t_date_str'] = old_target.strftime("%d/%m/%Y")
            context.user_data['t_target_time'] = old_target.strftime("%H:%M")
        txt = f"✏️ <b>[{major.upper()}] ID: {task_id} အား ပြင်ဆင်ခြင်း</b>"
    else:
        context.user_data['t_type'] = "tutorial" if "tutorial" in cmd else "task"
        context.user_data['t_major'] = major
        context.user_data.pop('t_edit_id', None)
        context.user_data.pop('t_reminder_lead_hours', None)
        context.user_data.pop('t_date_str', None)
        context.user_data.pop('t_target_time', None)
        txt = f"🎯 <b>[{major.upper()}] အသစ်ထည့်သွင်းခြင်း</b>"

    if not get_major_groups(major):
        env_key = next((key for slug, key in {
            "ceit": "CEIT_MAJOR", "ep": "EP_MAJOR", "ec": "EC_MAJOR", "archi": "ARCHI_MAJOR",
            "mc": "MC_MAJOR", "mech": "MECH_MAJOR", "civil": "CIVIL_MAJOR"
        }.items() if slug == major), f"{major.upper()}_MAJOR")
        await update.message.reply_text(
            f"⚠️ <b>{major.upper()} GP ID မသတ်မှတ်ရသေးပါ။</b> <code>.env</code> ထဲက <code>{env_key}</code> ကို သတ်မှတ်ပြီး restart မလုပ်မချင်း auto reminder မပို့ပါ။",
            parse_mode=ParseMode.HTML
        )

    subjects_list = await _get_subjects_for_major(major)
    subject_map = {str(idx): subject for idx, subject in enumerate(subjects_list)}
    context.user_data['t_subject_map'] = subject_map
    keyboard = []
    items = list(subject_map.items())
    for i in range(0, len(items), 2):
        keyboard.append([
            InlineKeyboardButton(subject[:25], callback_data=f"tsubj_{idx}")
            for idx, subject in items[i:i + 2]
        ])
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="task_cancel")])

    await update.message.reply_text(
        f"{txt}\n\nဘာသာရပ်ကို ရွေးချယ်ပါ -",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )
    return T_SUBJECT


async def t_subject_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    key = query.data.split("_", 1)[1]
    subject_map = context.user_data.get('t_subject_map', {})
    subject = subject_map.get(key, key)
    context.user_data['t_subject'] = subject
    await query.edit_message_text(
        f"✅ ဘာသာရပ်: <b>{_html_text(subject)}</b>\n\n📅 <b>Due Date ထည့်ပါ။</b> (DD/MM/YYYY ဥပမာ <code>28/10/2026</code>)",
        reply_markup=_task_cancel_markup(), parse_mode=ParseMode.HTML
    )
    return T_DATE


async def t_date_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    date_str = update.message.text.strip()
    try:
        datetime.strptime(date_str, "%d/%m/%Y")
    except ValueError:
        await update.message.reply_text("❌ Format မှားနေပါသည်။ DD/MM/YYYY ပုံစံဖြင့် ထပ်ရိုက်ပါ။", reply_markup=_task_cancel_markup())
        return T_DATE
    context.user_data['t_date_str'] = date_str
    await update.message.reply_text(
        f"✅ Due Date: <code>{date_str}</code>\n\n🕒 <b>Due Time ထည့်ပါ။</b> (24-hour format, HH:MM ဥပမာ <code>14:30</code>)",
        reply_markup=_task_cancel_markup(), parse_mode=ParseMode.HTML
    )
    return T_TIME


async def t_time_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    time_str = update.message.text.strip()
    mm_tz = timezone(timedelta(hours=6, minutes=30))
    try:
        parsed_time = datetime.strptime(time_str, "%H:%M").time()
    except ValueError:
        await update.message.reply_text("❌ အချိန်ပုံစံမှားနေပါသည်။ 24-hour HH:MM ဥပမာ <code>09:30</code> ကိုထည့်ပါ။", reply_markup=_task_cancel_markup(), parse_mode=ParseMode.HTML)
        return T_TIME

    date_str = context.user_data.get('t_date_str')
    try:
        date = datetime.strptime(date_str, "%d/%m/%Y").date()
        target_dt = datetime.combine(date, parsed_time).replace(tzinfo=mm_tz)
    except (ValueError, TypeError):
        await update.message.reply_text("❌ ရက်စွဲကို ပြန်ထည့်ပါ။", reply_markup=_task_cancel_markup())
        return T_DATE
    if target_dt <= datetime.now(mm_tz):
        await update.message.reply_text("❌ အတိတ်က အချိန်ကို သတ်မှတ်၍မရပါ။ Due Time ကို ပြန်ထည့်ပါ။", reply_markup=_task_cancel_markup())
        return T_TIME

    context.user_data['t_target_dt'] = target_dt
    context.user_data['t_target_time'] = target_dt.strftime("%H:%M")
    await update.message.reply_text(
        f"✅ Due Date/Time: <code>{target_dt.strftime('%d/%m/%Y %H:%M')}</code> (Myanmar Time)\n\n"
        "⏰ <b>ဘယ်နှစ်နာရီ ကြိုတင်သတိပေးရမလဲ?</b>\n"
        "နာရီအရေအတွက်ကို 1 မှ 720 ကြား ရိုက်ထည့်ပါ။ ဥပမာ <code>18</code> = 18 နာရီကြို။",
        reply_markup=_task_cancel_markup(), parse_mode=ParseMode.HTML
    )
    return T_LEAD_HOURS


async def t_lead_hours_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_hours = update.message.text.strip()
    if not raw_hours.isdigit() or not 1 <= int(raw_hours) <= 720:
        await update.message.reply_text("❌ နာရီကို 1 မှ 720 အတွင်း ဂဏန်းဖြင့် ထည့်ပါ။ ဥပမာ <code>18</code>။", reply_markup=_task_cancel_markup(), parse_mode=ParseMode.HTML)
        return T_LEAD_HOURS
    lead_hours = int(raw_hours)
    target_dt = context.user_data.get('t_target_dt')
    if target_dt and target_dt - timedelta(hours=lead_hours) <= datetime.now(target_dt.tzinfo):
        await update.message.reply_text("⚠️ သတ်မှတ်ထားတဲ့ ကြိုတင်ချိန်က အခုအချိန်ကျော်နေပါပြီ။ နာရီနည်းနည်းလျှော့ထည့်ပါ။", reply_markup=_task_cancel_markup())
        return T_LEAD_HOURS
    context.user_data['t_reminder_lead_hours'] = lead_hours
    await update.message.reply_text(
        f"✅ ကြိုတင်သတိပေးချိန်: <b>{lead_hours} နာရီ</b>\n\n📝 <b>Task/Tutorial အကြောင်းအရာကို ရိုက်ထည့်ပါ။</b>",
        reply_markup=_task_cancel_markup(), parse_mode=ParseMode.HTML
    )
    return T_CONTENT


async def t_content_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    content = update.message.text.strip()
    if not content:
        await update.message.reply_text("❌ အကြောင်းအရာ အလွတ်မဖြစ်ရပါ။ ပြန်ထည့်ပါ။", reply_markup=_task_cancel_markup())
        return T_CONTENT

    task_type = context.user_data.get('t_type')
    subject = context.user_data.get('t_subject')
    date_str = context.user_data.get('t_date_str')
    target_dt = context.user_data.get('t_target_dt')
    lead_hours = int(context.user_data.get('t_reminder_lead_hours', 72 if task_type == "tutorial" else 18))
    edit_id = context.user_data.get('t_edit_id')
    major = context.user_data.get('t_major') or await get_user_major(update.effective_user.id)
    if not major:
        await update.message.reply_text("⚠️ Major ကို အတည်ပြု၍ မရပါ။ ပြန်စတင်ပါ။")
        _clear_task_user_data(context)
        return ConversationHandler.END

    db = get_db(major=major)
    if db is None:
        await update.message.reply_text("❌ Database ချိတ်ဆက်မှု မအောင်မြင်ပါ။")
        _clear_task_user_data(context)
        return ConversationHandler.END
    if edit_id:
        await db.tasks.update_one(
            {"$and": [build_major_filter(major), {"short_id": edit_id}]},
            {"$set": {
                "major": major, "subject": subject, "date_str": date_str,
                "target_dt": target_dt, "time_str": target_dt.strftime("%H:%M"),
                "reminder_lead_hours": lead_hours, "content": content, "reminded": False
            }}
        )
        short_id = edit_id
        msg = f"✅ <b>[{major.upper()} | ID: {edit_id}] ပြင်ဆင်ပြီးပါပြီ။</b>"
    else:
        short_id = random.randint(1000, 9999)
        await db.tasks.insert_one({
            "major": major, "short_id": short_id, "type": task_type,
            "subject": subject, "date_str": date_str, "target_dt": target_dt,
            "time_str": target_dt.strftime("%H:%M"), "reminder_lead_hours": lead_hours,
            "content": content, "reminded": False
        })
        msg = f"✅ <b>[{major.upper()}] အောင်မြင်ပါသည်။</b> (ID: <code>{short_id}</code>)"

    target_label = target_dt.strftime("%d/%m/%Y %H:%M") if target_dt else f"{date_str} {context.user_data.get('t_target_time', '')}"
    await update.message.reply_text(
        f"{msg}\n\n📚 {_html_text(subject)}\n📅 Due: {_html_text(target_label)} (Myanmar Time)\n"
        f"⏰ ကြိုတင်သတိပေး: {lead_hours} နာရီ\n📝 {_html_text(content)}",
        parse_mode=ParseMode.HTML
    )
    _clear_task_user_data(context)
    return ConversationHandler.END


# ================= 🔔 TASKS & TUTORIALS REMINDER JOB (MULTI-MAJOR ROUTED) =================
async def tasks_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    """Send Task/Tutorial reminders only to the GP configured for that record's Major."""
    databases = get_all_major_dbs()
    if not databases:
        return

    mm_tz = timezone(timedelta(hours=6, minutes=30))
    now = datetime.now(mm_tz)

    for db_major, database in databases:
        if database is None:
            continue
        tasks = await database.tasks.find({"reminded": False, "target_dt": {"$gt": now}}).to_list(length=None)
        for task in tasks:
            target_dt = task.get("target_dt")
            if not isinstance(target_dt, datetime):
                continue
            if target_dt.tzinfo is None:
                target_dt = target_dt.replace(tzinfo=timezone.utc)
            target_dt = target_dt.astimezone(mm_tz)

            task_type = task.get("type", "task")
            resolved_major = normalize_major(task.get("major"))
            if not resolved_major and db_major != "legacy":
                resolved_major = normalize_major(db_major)
            if not resolved_major:
                logger.debug("Skipping task %s: Major is missing/unknown.", task.get("short_id"))
                continue

            try:
                lead_hours = int(task.get("reminder_lead_hours", 72 if task_type == "tutorial" else 18))
            except (TypeError, ValueError):
                lead_hours = 72 if task_type == "tutorial" else 18
            lead_hours = max(1, min(720, lead_hours))
            remind_time = target_dt - timedelta(hours=lead_hours)
            if not (remind_time <= now < target_dt):
                continue

            groups = get_major_groups(resolved_major)
            if not groups:
                # Leave reminded=False: if the Major GP is configured before the due time,
                # the job can still deliver the reminder. Never use CLASS_GROUP_ID as fallback.
                logger.debug("No GP configured for %s; task reminder %s was not sent.", resolved_major.upper(), task.get("short_id"))
                continue

            emoji = "📚" if task_type == "tutorial" else "📝"
            title = "TUTORIAL REMINDER" if task_type == "tutorial" else "ASSIGNMENT REMINDER"
            due_label = target_dt.strftime("%d/%m/%Y %I:%M %p")
            msg = (
                f"🔔 <b>[{resolved_major.upper()}] {title}</b>\n━━━━━━━━━━━━━━━━━━\n"
                f"{emoji} <b>ဘာသာရပ်:</b> {_html_text(task.get('subject', 'Unknown'))}\n"
                f"📅 <b>နောက်ဆုံးရက်:</b> <code>{_html_text(due_label)}</code> (Myanmar Time)\n"
                f"⏰ <b>ကြိုတင်သတိပေး:</b> {lead_hours} နာရီ\n\n"
                f"📖 <b>အကြောင်းအရာ:</b>\n{_html_text(task.get('content', ''))}\n\n"
                "<i>(သက်ဆိုင်ရာ Major Group အတွက်သာ အလိုအလျောက် သတိပေးခြင်း ဖြစ်ပါသည်။)</i>"
            )
            delivered = False
            for gid in groups:
                try:
                    await context.bot.send_message(chat_id=gid, text=msg, parse_mode=ParseMode.HTML)
                    delivered = True
                except Exception:
                    logger.exception("Failed sending %s reminder %s to group %s", resolved_major.upper(), task.get("short_id"), gid)
            if delivered:
                await database.tasks.update_one({"_id": task["_id"]}, {"$set": {"reminded": True}})


# ================= ⚙️ ADMIN TIMETABLE MANAGEMENT =================
async def add_class(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 6:
        return await update.message.reply_text(
            "⚠️ <code>/addclass &lt;Day&gt; &lt;Time&gt; &lt;Major&gt; &lt;Room&gt; &lt;Teacher&gt; &lt;Subject&gt;</code>",
            parse_mode=ParseMode.HTML
        )

    day, time_str, raw_major, room, teacher, subject = (
        args[0], args[1], args[2], args[3], args[4], " ".join(args[5:])
    )
    major = "all" if raw_major.strip().lower() == "all" else normalize_major(raw_major)
    if not major:
        return await update.message.reply_text("❌ Major မမှန်ပါ။ CEIT, EP, EC, ARCHI, MC, MECH, CIVIL သို့မဟုတ် ALL ကို သုံးပါ။")
    if not await _can_manage_timetable(update, context, major):
        return

    targets = get_all_major_dbs() if major == "all" else [(major, get_db(major=major))]
    targets = [(m, database) for m, database in targets if database is not None]
    if not targets:
        return await update.message.reply_text("❌ Database ချိတ်ဆက်မှု မအောင်မြင်ပါ။")

    period = {"time": time_str, "room": room, "teacher": teacher, "subject": subject}
    for _, database in targets:
        await database.timetable.update_one(
            {"day": day, "major": major},
            {"$push": {"periods": period}},
            upsert=True
        )
    await update.message.reply_text(
        f"✅ {_html_text(day)} တွင် <b>{_html_text(major.upper())}</b> အတွက် အတန်းချိန်အသစ် သွင်းပြီးပါပြီ။",
        parse_mode=ParseMode.HTML
    )


async def del_class(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 3:
        return await update.message.reply_text(
            "⚠️ `/delclass <Day> <Time> <Major>`",
            parse_mode=ParseMode.HTML
        )

    raw_major = args[2]
    major = "all" if raw_major.strip().lower() == "all" else normalize_major(raw_major)
    if not major:
        return await update.message.reply_text("❌ Major မမှန်ပါ။ CEIT, EP, EC, ARCHI, MC, MECH, CIVIL သို့မဟုတ် ALL ကို သုံးပါ။")
    if not await _can_manage_timetable(update, context, major):
        return

    targets = get_all_major_dbs() if major == "all" else [(major, get_db(major=major))]
    for _, database in targets:
        if database is not None:
            await database.timetable.update_many(
                {"day": args[0], "major": {"$in": _get_timetable_mutation_majors(major) if major != "all" else ["all"]}},
                {"$pull": {"periods": {"time": args[1]}}}
            )
    await update.message.reply_text(f"🗑️ {args[0]} မှ {args[1]} ({major.upper()}) အတန်းကို ဖျက်လိုက်ပါပြီ။")


async def clear_day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        return await update.message.reply_text(
            "⚠️ အသုံးပြုနည်း: `/clearday <Day> <Major>`",
            parse_mode=ParseMode.HTML
        )

    day = context.args[0]
    raw_major = context.args[1]
    major = "all" if raw_major.strip().lower() == "all" else normalize_major(raw_major)
    if not major:
        return await update.message.reply_text("❌ Major မမှန်ပါ။ CEIT, EP, EC, ARCHI, MC, MECH, CIVIL သို့မဟုတ် ALL ကို သုံးပါ။")
    if not await _can_manage_timetable(update, context, major):
        return

    targets = get_all_major_dbs() if major == "all" else [(major, get_db(major=major))]
    for _, database in targets:
        if database is not None:
            await database.timetable.update_many(
                {"day": day, "major": {"$in": _get_timetable_mutation_majors(major) if major != "all" else ["all"]}},
                {"$set": {"periods": []}}
            )
    await update.message.reply_text(
        f"🧹 {_html_text(day)} ၏ <b>{_html_text(major.upper())}</b> အတန်းချိန်အားလုံးကို ရှင်းလင်းလိုက်ပါပြီ။",
        parse_mode=ParseMode.HTML
    )