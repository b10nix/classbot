# handlers/academic/attendance.py

import random
import logging
from datetime import datetime, timedelta, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode

from database.db import get_db, get_all_major_dbs
from services.major_service import (
    DEFAULT_MAJOR,
    get_user_major,
    is_major_authorized,
    extract_major_from_roll,
    find_student_record_by_user_id,
    normalize_major
)
from services.finance_service import (
    build_major_filter,
    get_short_roll,
    extract_roll_number
)

logger = logging.getLogger(__name__)

ROLL_PIN_INPUT = 1


async def is_attendance_admin(user_id: int, major: str | None = None) -> bool:
    if await is_major_authorized(user_id, major):
        return True

    db = get_db()
    if db is not None:
        approver = await db.approvers.find_one({"user_id": user_id})
        if approver:
            return True
    return False


async def start_attendance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    major = await get_user_major(user_id)
    if not major:
        return await update.message.reply_text("⚠️ Major ကို အတည်ပြု၍ မရပါ။ Admin ထံ ဆက်သွယ်ပါ။")

    if not await is_attendance_admin(user_id, major):
        return await update.message.reply_text("⚠️ သက်ဆိုင်ရာ Major EC များနှင့် Admin များသာ သုံးခွင့်ရှိပါသည်။")

    db = get_db(major=major)
    if db is None:
        return await update.message.reply_text("❌ Database ချိတ်ဆက်မှု မအောင်မြင်ပါ။")

    pin_code = str(random.randint(100000, 999999))
    while await db.attendance_records.find_one({"pin_code": pin_code, "status": "active"}):
        pin_code = str(random.randint(100000, 999999))

    mm_tz = timezone(timedelta(hours=6, minutes=30))
    today_str = datetime.now(mm_tz).strftime("%Y-%m-%d")

    m_filter = build_major_filter(major)
    await db.attendance_records.update_many(
        {"$and": [m_filter, {"status": "active"}]},
        {"$set": {"status": "closed"}}
    )

    record = {
        "major": major,
        "date": today_str,
        "pin_code": pin_code,
        "status": "active",
        "created_by": user_id,
        "present_users": []
    }
    await db.attendance_records.insert_one(record)

    kb = [[InlineKeyboardButton("🛑 CLOSE ATTENDANCE (KILL)", callback_data=f"att_close_{major}_{pin_code}")]]
    await update.message.reply_text(
        f"🎲 <b>[{major.upper()}] ROLL CALL STARTED</b>\n\n"
        f"🔢 Secret PIN: <code>{pin_code}</code>\n"
        f"📅 Date: <b>{today_str}</b>\n\n"
        f"<i>{major.upper()} ကျောင်းသားများအား Bot တွင် /rollcall နှိပ်၍ ဤ PIN ရိုက်ထည့်ရန် အော်ပေးလိုက်ပါဗျာ။</i>",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.HTML
    )


# Inside handlers/academic/attendance.py:
# Replace close_attendance_action and the record lookup in dates_callback_handler with:

async def close_attendance_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query.data.startswith("att_close_"):
        return
    parts = query.data.split("_")
    encoded_major = normalize_major(parts[2]) if len(parts) >= 4 else None
    pin = parts[-1]
    candidates = get_all_major_dbs()
    if encoded_major:
        target_db = get_db(major=encoded_major)
        candidates = [(encoded_major, target_db)] if target_db is not None else []
    found = None
    for db_major, database in candidates:
        if database is None:
            continue
        record = await database.attendance_records.find_one({"pin_code": pin, "status": "active"})
        if not record:
            record = await database.attendance_records.find_one({"pin_code": pin})
        if record:
            actual_major = normalize_major(record.get("major")) or (db_major if db_major != "legacy" else None)
            if encoded_major and actual_major and actual_major != encoded_major:
                continue
            found = (database, record, actual_major)
            break
    if not found:
        return await query.answer("⚠️ Attendance record မတွေ့ပါ။", show_alert=True)

    db, record, record_major = found
    if not record_major:
        return await query.answer("⛔ Attendance Major မသိနိုင်ပါ။ Admin က record ကို စစ်ဆေးပေးပါ။", show_alert=True)
    if not await is_attendance_admin(update.effective_user.id, record_major):
        return await query.answer("⛔ ဤ Major ၏ Attendance ကို ပိတ်သိမ်းခွင့် မရှိပါ။", show_alert=True)

    await query.answer()
    await db.attendance_records.update_one({"_id": record["_id"]}, {"$set": {"status": "closed"}})
    await query.edit_message_text(
        f"🛑 <b>[{record_major.upper()}] ATTENDANCE CLOSED</b>\n\n"
        f"PIN Code (<code>{pin}</code>) ကို ပိတ်သိမ်းလိုက်ပါပြီ။ စာရင်းသွင်း၍ မရတော့ပါ။",
        parse_mode=ParseMode.HTML
    )


async def rollcall_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    student, student_major = await find_student_record_by_user_id(user_id)
    if not student or not student_major:
        student = None

    if not student:
        await update.message.reply_text(
            "❌ <b>ခွင့်ပြုချက်မရှိပါ</b>\n\n"
            "သင့်အား ကျောင်းသားစာရင်းသွင်းထားခြင်း မရှိသေးပါ။ <code>/register</code> ဖြင့် အရင်စာရင်းသွင်းပါ။",
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END

    st_major = (student_major or student.get("major") or extract_major_from_roll(student.get("roll_no", "")))
    if not st_major:
        await update.message.reply_text("⚠️ သင့် Major ကို အတည်ပြု၍ မရပါ။ Admin ထံ ဆက်သွယ်ပါ။")
        return ConversationHandler.END
    st_major = st_major.upper()
    await update.message.reply_text(
        f"🔢 <b>[{st_major}] ROLL CALL ATTENDANCE</b>\n\n"
        "အတန်းထဲရှိ ကျောက်သင်ပုန်းမှ <b>6-Digit PIN Code</b> ကို ရိုက်ထည့်ပေးပါဗျာ:",
        parse_mode=ParseMode.HTML
    )
    return ROLL_PIN_INPUT


async def rollcall_receive_pin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pin_input = update.message.text.strip()
    user_id = update.effective_user.id
    student, student_major = await find_student_record_by_user_id(user_id)
    if not student or not student_major:
        await update.message.reply_text("❌ ကျောင်းသားစာရင်း ရှာမတွေ့ပါ သို့မဟုတ် Major ကို အတည်ပြု၍ မရပါ။")
        return ConversationHandler.END

    db = get_db(major=student_major)
    if db is None:
        await update.message.reply_text("❌ Database ချိတ်ဆက်မှု မအောင်မြင်ပါ။")
        return ConversationHandler.END

    record = await db.attendance_records.find_one({
        "$and": [
            build_major_filter(student_major),
            {"pin_code": pin_input, "status": "active"}
        ]
    })
    if not record:
        await update.message.reply_text(
            f"❌ <b>[{student_major.upper()}]</b> အတွက် PIN ကုဒ်မှားယွင်းနေပါသည် သို့မဟုတ် စာရင်းပိတ်သွားပါပြီ။\n"
            "/rollcall ဖြင့် ပြန်စမ်းပါ။",
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END

    present_list = record.get("present_users", [])
    stu_internal_id = student.get("internal_id")
    if any(
        u.get("user_id") == user_id or (stu_internal_id and u.get("internal_id") == stu_internal_id)
        for u in present_list
    ):
        await update.message.reply_text("⚠️ သင်သည် ဤအတန်းအတွက် စာရင်းသွင်းပြီးသား ဖြစ်ပါသည်ဗျာ။")
        return ConversationHandler.END

    mm_tz = timezone(timedelta(hours=6, minutes=30))
    now_time = datetime.now(mm_tz).strftime("%I:%M %p")

    user_data = {
        "internal_id": stu_internal_id,
        "user_id": user_id,
        "roll_number": extract_roll_number(student),
        "roll_no": student.get("roll_no", "Unknown"),
        "semester": student.get("current_semester"),
        "name": student.get("name", "Unknown"),
        "time": now_time
    }

    await db.attendance_records.update_one(
        {"_id": record["_id"]},
        {"$push": {"present_users": user_data}}
    )

    short_roll = get_short_roll(student)
    await update.message.reply_text(
        f"✅ <b>[{student_major.upper()}] ROLL CALL အောင်မြင်ပါသည်</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"🆔 Roll No: <b>{short_roll}</b> (<code>{student.get('roll_no', '')}</code>)\n"
        f"👤 Name: <b>{student.get('name', 'Unknown')}</b>\n"
        f"🕒 Time: <code>{now_time}</code>\n"
        f"📅 Date: <code>{record.get('date', '')}</code>",
        parse_mode=ParseMode.HTML
    )
    return ConversationHandler.END


async def rollcall_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚫 Cancelled.")
    return ConversationHandler.END


async def view_dates_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    major = await get_user_major(user_id)
    if not major or not await is_attendance_admin(user_id, major):
        return
    await show_dates_page(update, context, page=0, major=major)


async def show_dates_page(update_or_query, context: ContextTypes.DEFAULT_TYPE, page: int, major: str | None = None):
    user_id = update_or_query.effective_user.id if isinstance(update_or_query, Update) else update_or_query.from_user.id
    if not major:
        major = await get_user_major(user_id)
    if not major:
        return
    db = get_db(major=major)
    if db is None:
        return

    m_filter = build_major_filter(major)
    cursor = db.attendance_records.find(m_filter).sort("_id", -1)
    all_records = await cursor.to_list(length=None)

    if not all_records:
        msg_text = (
            f"📅 <b>[{major.upper()}] ATTENDANCE HISTORY</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🎉 ကောက်ထားသည့် စာရင်းများ မရှိသေးပါဗျာ။"
        )
        if isinstance(update_or_query, Update):
            await update_or_query.message.reply_text(msg_text, parse_mode=ParseMode.HTML)
        else:
            await update_or_query.edit_message_text(msg_text, parse_mode=ParseMode.HTML)
        return

    per_page = 10
    total_pages = (len(all_records) + per_page - 1) // per_page
    start = page * per_page
    end = start + per_page
    page_records = all_records[start:end]

    kb = []
    for r in page_records:
        date_str = r.get("date", "Unknown Date")
        pin = r.get("pin_code", "000000")
        count = len(r.get("present_users", []))
        kb.append([
            InlineKeyboardButton(
                f"📅 {date_str} (PIN: {pin}) -> Present: {count}",
                callback_data=f"vdate_show_{pin}_{page}"
            )
        ])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"vdate_p_{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"vdate_p_{page+1}"))
    if nav:
        kb.append(nav)

    kb.append([InlineKeyboardButton("❌ Close", callback_data="vdate_close")])

    msg_text = (
        f"📅 <b>[{major.upper()}] ATTENDANCE HISTORY (Page: {page+1}/{total_pages})</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"စာရင်းကြည့်လိုသည့် ရက်စွဲအား နှိပ်ပါ -"
    )

    if isinstance(update_or_query, Update):
        await update_or_query.message.reply_text(msg_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    else:
        await update_or_query.edit_message_text(msg_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)


async def dates_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = update.effective_user.id
    major = await get_user_major(user_id)
    if not major:
        return await query.answer("⚠️ Major ကို အတည်ပြု၍ မရပါ။", show_alert=True)

    if not await is_attendance_admin(user_id, major):
        return await query.answer("⛔ Access Denied", show_alert=True)

    await query.answer()

    if data == "vdate_close":
        return await query.edit_message_text("✅ Closed History.")

    if data.startswith("vdate_p_"):
        page = int(data.split("_")[2])
        return await show_dates_page(query, context, page, major=major)

    db = get_db(major=major)
    if db is None:
        return

    if data.startswith("vdate_show_") or data.startswith("vdate_abs_"):
        parts = data.split("_", 3)
        mode = parts[1]
        pin = parts[2]
        page = int(parts[3])

        m_filter = build_major_filter(major)
        record = await db.attendance_records.find_one(
            {"$and": [m_filter, {"pin_code": pin}]},
            sort=[("_id", -1)]
        )
        if not record:
            return await query.edit_message_text("⚠️ မှတ်တမ်း ရှာမတွေ့တော့ပါ။")

        record_major = record.get("major") or major
        present_list = record.get("present_users", [])

        if mode == "show":
            txt = f"📅 <b>[{record_major.upper()}] ATTENDANCE: {record.get('date', '')}</b>\n"
            txt += f"🔢 PIN Code: <code>{pin}</code>\n"
            txt += f"🟢 တက်ရောက်သူ (Present: {len(present_list)} ဦး):\n━━━━━━━━━━━━━━━━━━\n"

            present_list.sort(key=lambda x: extract_roll_number(x))

            if not present_list:
                txt += "<i>(တက်ရောက်သူ မရှိပါ)</i>"
            for u in present_list:
                txt += f"✅ {get_short_roll(u)} - {u.get('name', '')} (<code>{u.get('time', '')}</code>)\n"

            kb = [
                [InlineKeyboardButton("❌ ပျက်ကွက်သူ (Absent) စာရင်းကြည့်ရန်", callback_data=f"vdate_abs_{pin}_{page}")],
                [InlineKeyboardButton("🔙 Back to Dates", callback_data=f"vdate_p_{page}")]
            ]
            await query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)

        elif mode == "abs":
            all_students = await db.student_info.find(build_major_filter(record_major)).to_list(length=None)

            present_internal_ids = {u.get("internal_id") for u in present_list if u.get("internal_id")}
            present_ids = {u.get("user_id") for u in present_list if u.get("user_id")}
            present_roll_nums = {extract_roll_number(u) for u in present_list if extract_roll_number(u) != 9999}

            absent_list = []
            for s in all_students:
                s_internal = s.get("internal_id")
                s_id = s.get("user_id") or s.get("tg_id")
                s_rnum = extract_roll_number(s)
                if (
                    (s_internal and s_internal in present_internal_ids)
                    or (s_id and s_id in present_ids)
                    or (s_rnum != 9999 and s_rnum in present_roll_nums)
                ):
                    continue
                absent_list.append(s)

            absent_list.sort(key=lambda x: extract_roll_number(x))

            txt = f"📅 <b>[{record_major.upper()}] ATTENDANCE: {record.get('date', '')}</b>\n"
            txt += f"🔢 PIN Code: <code>{pin}</code>\n"
            txt += f"🔴 ပျက်ကွက်သူ (Absent: {len(absent_list)} ဦး):\n━━━━━━━━━━━━━━━━━━\n"

            if not absent_list:
                txt += "🎉 <i>ကျောင်းသားအားလုံး တက်ရောက်ကြသည်။ (All Present!)</i>"
            for s in absent_list:
                txt += f"❌ {get_short_roll(s)} - {s.get('name', '')}\n"

            kb = [
                [InlineKeyboardButton("🟢 တက်ရောက်သူ (Present) စာရင်းကြည့်ရန်", callback_data=f"vdate_show_{pin}_{page}")],
                [InlineKeyboardButton("🔙 Back to Dates", callback_data=f"vdate_p_{page}")]
            ]
            await query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)