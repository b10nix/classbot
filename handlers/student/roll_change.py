"""EC/Super Admin force-update workflow for a student's current roll number."""
from __future__ import annotations

import html
import logging
import re

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from database.db import get_db
from services.finance_service import build_major_filter
from services.major_service import get_user_major, is_major_authorized, normalize_major, extract_major_from_roll
from services.roll_number_service import (
    RollNumberError,
    SemesterNotConfiguredError,
    format_roll_number,
    get_current_semester,
    get_mm_now_str,
    normalize_roll_input,
    parse_roll_number,
)
from utils.auth import is_super_admin

logger = logging.getLogger(__name__)


def _document_major(student: dict) -> str:
    """Resolve a record's Major without mistaking legacy non-CEIT records for CEIT."""
    stored = normalize_major(student.get("major"))
    if stored:
        return stored
    for field in ("roll_no", "legacy_roll_no"):
        inferred = extract_major_from_roll(str(student.get(field) or ""))
        if inferred:
            return inferred
    # Legacy numeric-only untagged records are treated as CEIT by the existing
    # build_major_filter compatibility rule.
    return "ceit"


def _current_roll_number(student: dict, fallback: int) -> int:
    value = student.get("roll_number")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    try:
        return parse_roll_number(str(student.get("roll_no") or fallback))["roll_number"]
    except RollNumberError:
        return fallback


async def force_roll_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Force a roll change for a student in a Major the caller is authorized to manage.

    Usage:
      /forceroll <old_roll> <new_roll>             (the caller's active/assigned Major)
      /forceroll <major> <old_roll> <new_roll>     (any Major the caller is authorized for)

    Student records retain their previous roll in `roll_history`; payment data is untouched.
    """
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if message is None or user is None:
        return

    if chat is not None and chat.type != "private":
        await message.reply_text(
            "🔒 ဒီလုပ်ဆောင်ချက်ကို Student အချက်အလက်မပေါက်ကြားစေရန် Bot DM ထဲမှာပဲ အသုံးပြုပါ။"
        )
        return

    args = list(context.args or [])
    is_sa = is_super_admin(user.id)
    target_major = None
    old_raw = None
    new_raw = None

    if len(args) == 2:
        target_major = await get_user_major(user.id)
        old_raw, new_raw = args
        if target_major is None:
            await message.reply_text(
                "⚠️ သင့် Active Major မသတ်မှတ်ထားပါ သို့မဟုတ် Major အများအပြားအတွက် EC ဖြစ်နေပါသည်။\n"
                "အသုံးပြုရန်: <code>/forceroll &lt;major&gt; &lt;old_roll&gt; &lt;new_roll&gt;</code>",
                parse_mode=ParseMode.HTML,
            )
            return
    elif len(args) == 3:
        target_major = normalize_major(args[0])
        old_raw, new_raw = args[1], args[2]
        if target_major is None:
            await message.reply_text(
                "⚠️ Major code မမှန်ပါ။ CEIT, EC, EP, CIVIL, MECH/ME, MC, ARCHI ထဲမှ ရွေးပါ။"
            )
            return
    else:
        await message.reply_text(
            "🛠️ <b>EC Force Roll Change</b>\n\n"
            "မိမိတာဝန်ယူထားသော Major:\n"
            "<code>/forceroll &lt;old_roll&gt; &lt;new_roll&gt;</code>\n"
            "ဥပမာ: <code>/forceroll 43 28</code>\n\n"
            "Major ကို တိတိကျကျရွေးရန်:\n"
            "<code>/forceroll &lt;major&gt; &lt;old_roll&gt; &lt;new_roll&gt;</code>\n"
            "ဥပမာ: <code>/forceroll mc 10 18</code>\n\n"
            "⚠️ သက်ဆိုင်ရာ Major EC သို့မဟုတ် Super Admin သာ သုံးနိုင်ပါသည်။",
            parse_mode=ParseMode.HTML,
        )
        return

    if not await is_major_authorized(user.id, target_major):
        await message.reply_text("⛔ ဒီ Major ရဲ့ Roll Number ကို ပြင်ဆင်ခွင့်မရှိပါ။")
        return

    try:
        parsed_old = parse_roll_number(old_raw)
        if parsed_old.get("major") and parsed_old["major"] != target_major:
            await message.reply_text(
                f"⛔ ထည့်ထားသော Old Roll သည် {parsed_old['major'].upper()} ဖြစ်ပြီး target Major က "
                f"{target_major.upper()} ဖြစ်နေပါသည်။"
            )
            return
        old_number = parsed_old["roll_number"]
        new_number, new_roll_str = normalize_roll_input(new_raw)
        semester = await get_current_semester(target_major, strict=True)
        canonical_new = format_roll_number(target_major, semester, new_number)
    except SemesterNotConfiguredError as exc:
        await message.reply_text(str(exc), parse_mode=ParseMode.HTML)
        return
    except RollNumberError as exc:
        await message.reply_text(f"⚠️ Roll Number မှားနေပါသည်: {html.escape(str(exc))}", parse_mode=ParseMode.HTML)
        return

    db = get_db(major=target_major)
    if db is None:
        await message.reply_text("❌ Database ချိတ်ဆက်မှု မအောင်မြင်ပါ။")
        return

    major_filter = build_major_filter(target_major)
    old_roll_regex = re.compile(rf"(?:^|-)0*{old_number}$", re.IGNORECASE)
    old_query = {
        "$and": [
            major_filter,
            {"$or": [{"roll_number": old_number}, {"roll_no": old_roll_regex}]},
        ]
    }
    candidates = await db.student_info.find(old_query).to_list(length=None)
    matches = [candidate for candidate in candidates if _document_major(candidate) == target_major][:2]
    if not matches:
        await message.reply_text(
            f"❌ [{target_major.upper()}] လက်ရှိ Roll <code>{html.escape(old_raw)}</code> နဲ့ Student မတွေ့ပါ။",
            parse_mode=ParseMode.HTML,
        )
        return
    if len(matches) > 1:
        await message.reply_text(
            f"⚠️ [{target_major.upper()}] Roll {old_number:02d} နဲ့ Student records အများအပြားတွေ့ပါတယ်။ "
            "အရင်ဆုံး duplicate data ကို ပြင်ဆင်ပါ။"
        )
        return

    student = matches[0]
    new_roll_regex = re.compile(rf"(?:^|-)0*{new_number}$", re.IGNORECASE)
    duplicate_query = {
        "$and": [
            major_filter,
            {"_id": {"$ne": student["_id"]}},
            {"$or": [{"roll_number": new_number}, {"roll_no": new_roll_regex}]},
        ]
    }
    duplicate_candidates = await db.student_info.find(duplicate_query).to_list(length=None)
    duplicate = next((candidate for candidate in duplicate_candidates if _document_major(candidate) == target_major), None)
    if duplicate:
        await message.reply_text(
            f"❌ [{target_major.upper()}] Roll <code>{new_roll_str}</code> ကို "
            f"{html.escape(str(duplicate.get('name') or 'အခြား Student'))} က သုံးထားပြီးသားပါ။",
            parse_mode=ParseMode.HTML,
        )
        return

    old_semester = student.get("current_semester")
    current_old_number = _current_roll_number(student, old_number)
    current_old_str = str(student.get("roll_number_str") or f"{current_old_number:02d}")
    old_roll_display = str(student.get("roll_no") or current_old_str)
    if current_old_number == new_number and old_semester == semester:
        await message.reply_text("ℹ️ Roll Number နဲ့ Semester က သတ်မှတ်ထားပြီးသား ဖြစ်ပါတယ်။")
        return

    now = get_mm_now_str()
    history_entry = {
        "semester": old_semester,
        "roll_no": old_roll_display,
        "roll_number": current_old_number,
        "roll_number_str": current_old_str,
        "archived_at": now,
        "changed_by": user.id,
        "changed_by_username": user.username or "",
        "change_source": "ec_force_roll_change",
    }

    await db.student_info.update_one(
        {"_id": student["_id"]},
        {
            "$set": {
                "major": target_major,
                "current_semester": semester,
                "roll_number": new_number,
                "roll_number_str": new_roll_str,
                "roll_no": new_roll_str,
                "updated_at": now,
                "roll_last_changed_by": user.id,
                "roll_last_changed_at": now,
            },
            "$push": {"roll_history": history_entry},
        },
    )

    try:
        await db.student_roll_audit.insert_one({
            "action": "ec_force_roll_change",
            "major": target_major,
            "internal_id": student.get("internal_id"),
            "student_tg_id": student.get("tg_id") or student.get("user_id"),
            "student_name": student.get("name"),
            "old_roll_no": old_roll_display,
            "old_roll_number": current_old_number,
            "old_semester": old_semester,
            "new_roll_no": new_roll_str,
            "new_roll_number": new_number,
            "new_semester": semester,
            "changed_by": user.id,
            "changed_by_username": user.username or "",
            "timestamp": now,
        })
    except Exception:
        # The student record update is already complete; audit failures should be logged
        # separately rather than falsely reporting that the roll update failed.
        logger.exception("Could not create roll-change audit record for student %s", student.get("_id"))

    student_name = html.escape(str(student.get("name") or "Unknown"))
    old_display = html.escape(f"{old_roll_display} (Sem {old_semester or '?'})")
    await message.reply_text(
        "✅ <b>EC Force Roll Change အောင်မြင်ပါသည်။</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"👤 Student: <b>{student_name}</b>\n"
        f"🏛 Major: <b>{target_major.upper()}</b>\n"
        f"📚 Semester: <b>{semester}</b>\n"
        f"🔁 Old Roll: <code>{old_display}</code>\n"
        f"✅ New Roll: <code>{html.escape(canonical_new)}</code>\n\n"
        "Roll History မှာ အဟောင်းကို သိမ်းထားပြီး Fund/Payment history ကို မပြောင်းထားပါ။",
        parse_mode=ParseMode.HTML,
    )
