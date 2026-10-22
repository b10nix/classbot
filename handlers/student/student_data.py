# handlers/student/student_data.py

import html
import re
import uuid
import logging
from telegram import Update, InlineQueryResultArticle, InputTextMessageContent
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from database.db import get_db
from utils.auth import is_super_admin
from services.major_service import get_user_major, is_major_authorized, extract_major_from_roll, normalize_major
from services.roll_number_service import (
    RollNumberError,
    SemesterNotConfiguredError,
    parse_roll_number,
    get_current_semester,
    VALID_MAJORS
)
from services.finance_service import build_major_filter, build_fund_major_filter, extract_paid_amount, get_funds_metadata, get_short_roll

logger = logging.getLogger(__name__)


async def get_active_funds_for_major(major: str) -> list[dict]:
    """Returns non-archived active funds for the specified major."""
    all_f, archived_ids, _, _ = await get_funds_metadata(major)
    return [
        f for f in all_f
        if f.get("fund_id") and str(f["fund_id"]).lower() not in archived_ids
    ]


def build_fund_text(student_paid: dict, funds: list) -> str:
    if not funds:
        return "<i>လက်ရှိ ကောက်ခံနေသော ရန်ပုံငွေ မရှိသေးပါ</i>"

    text = ""
    for f in funds:
        f_id = f.get("fund_id")
        if not f_id:
            continue
        f_name = html.escape(str(f.get("fund_name", f_id)))
        target = int(f.get("target_amount", 0))
        paid = extract_paid_amount(student_paid.get(f_id, 0))

        if paid >= target:
            text += f"✅ <b>{f_name}:</b> {paid:,} / {target:,} MMK\n"
        elif paid > 0:
            remain = target - paid
            text += f"🟡 <b>{f_name}:</b> {paid:,} / {target:,} MMK (⚠️ {remain:,} လိုသေး)\n"
        else:
            text += f"🔴 <b>{f_name}:</b> 0 / {target:,} MMK (⚠️ {target:,} လိုသေး)\n"
    return text


def build_student_message(student: dict, funds: list, include_meta: bool = False) -> str:
    name = html.escape(str(student.get("name", "Unknown")))
    roll = html.escape(get_short_roll(student) or str(student.get("roll_no", "Unknown")))
    major_raw = student.get("major") or extract_major_from_roll(str(student.get("roll_no", ""))) or "UNKNOWN"
    major = html.escape(str(major_raw).upper())
    sem = student.get("current_semester")
    sem_str = f" · Sem {sem}" if sem else ""
    phone = html.escape(str(student.get("phone", "No Phone")))
    hostel = "Yes" if str(student.get("hostel", "")).lower() in ["yes", "true", "1"] else "No"

    tg_username = student.get("tg_username")
    tg_id = student.get("user_id") or student.get("tg_id")

    # Prefer the public username URL whenever a username is known. The numeric
    # tg:// link is only a fallback for users without a usable username.
    clean_uname = str(tg_username or "").strip().lstrip("@")
    if clean_uname and clean_uname.lower() not in {"none", "unknown"}:
        safe_uname = html.escape(clean_uname, quote=True)
        tg_link = f"<a href='https://t.me/{safe_uname}'>👤 View Profile (@{safe_uname})</a>"
    elif tg_id:
        tg_link = f"<a href='tg://user?id={tg_id}'>👤 View Profile</a>"
    else:
        tg_link = "Not Linked"

    meta_lines = ""
    if include_meta:
        assigned_ec = student.get("assigned_ec")
        if assigned_ec:
            meta_lines += f"👮‍♂️ <b>Assigned EC:</b> @{html.escape(str(assigned_ec))}\n"
        internal_id = student.get("internal_id")
        if internal_id:
            meta_lines += f"🆔 <b>Student ID:</b> <code>{html.escape(str(internal_id))}</code>\n"

    fund_text = build_fund_text(student.get("paid_funds", {}), funds)

    return (
        f"👤 <b>Name:</b> {name}\n"
        f"🎓 <b>Roll No:</b> <code>{roll}</code> (<b>{major}{sem_str}</b>)\n"
        f"🏠 <b>Hostel:</b> {hostel}\n"
        f"📞 <b>Phone:</b> <code>{phone}</code>\n"
        f"💬 <b>Telegram:</b> {tg_link}\n"
        f"{meta_lines}"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>Fund Status ({major}):</b>\n{fund_text}"
    )


def parse_inline_search_query(raw_query: str, user_major: str | None, super_admin: bool) -> dict:
    """Parse an inline query into a Major-scoped or global search plan.

    Super Admin behavior:
      - `10` searches current roll 10 across all seven majors.
      - `.mc 10` searches MC only; `.me 10` is an alias for MECH.
      - A full roll string such as `Sem-III-CEIT-10` scopes to that Major.
      - Plain names search all majors unless prefixed with a Major.

    EC behavior remains restricted to their own assigned Major.
    """
    query = str(raw_query or "").strip()
    if not query:
        return {"query": "", "target_major": user_major, "error": "empty_query", "roll_number": None}

    requested_major = None
    prefix_match = re.match(r"^\.([A-Za-z]+)(?:\s+(.*))?$", query)
    if prefix_match:
        alias = prefix_match.group(1)
        requested_major = normalize_major(alias)
        if requested_major is None:
            return {"query": "", "target_major": None, "error": "invalid_major", "roll_number": None}
        query = (prefix_match.group(2) or "").strip()
        if not query:
            return {"query": "", "target_major": requested_major, "error": "empty_query", "roll_number": None}

    parsed_roll = None
    try:
        parsed_roll = parse_roll_number(query)
    except RollNumberError:
        pass

    embedded_major = parsed_roll.get("major") if parsed_roll else None
    if embedded_major:
        if requested_major and requested_major != embedded_major:
            return {"query": query, "target_major": None, "error": "major_mismatch", "roll_number": None}
        requested_major = embedded_major

    if super_admin:
        target_major = requested_major  # None means all majors.
    else:
        if not user_major:
            return {"query": query, "target_major": None, "error": "no_major", "roll_number": None}
        if requested_major and requested_major != user_major:
            return {"query": query, "target_major": None, "error": "denied", "roll_number": None}
        target_major = user_major

    return {
        "query": query,
        "target_major": target_major,
        "error": None,
        "roll_number": parsed_roll["roll_number"] if parsed_roll else None,
    }


async def inline_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.inline_query.from_user.id
    is_sa = is_super_admin(user_id)
    user_major = await get_user_major(user_id)

    if not await is_major_authorized(user_id):
        result = [
            InlineQueryResultArticle(
                id=str(uuid.uuid4()),
                title="⛔ Access Denied",
                description="Admin နှင့် EC များသာ Search အသုံးပြုခွင့်ရှိပါသည်။",
                input_message_content=InputTextMessageContent(
                    "⛔ <b>Access Denied:</b> You do not have clearance to view student records.",
                    parse_mode=ParseMode.HTML
                )
            )
        ]
        return await update.inline_query.answer(result, cache_time=1, is_personal=True)

    plan = parse_inline_search_query(update.inline_query.query, user_major, is_sa)
    if plan["error"]:
        if plan["error"] == "denied":
            result = [InlineQueryResultArticle(
                id=str(uuid.uuid4()),
                title="⛔ Major Filter Denied",
                description="EC များသည် မိမိတာဝန်ယူထားသော Major ကိုသာ ရှာနိုင်ပါသည်။",
                input_message_content=InputTextMessageContent(
                    "⛔ <b>Major Filter Denied:</b> You can only search your assigned Major.",
                    parse_mode=ParseMode.HTML
                )
            )]
            return await update.inline_query.answer(result, cache_time=1, is_personal=True)
        if plan["error"] in {"invalid_major", "major_mismatch"}:
            result = [InlineQueryResultArticle(
                id=str(uuid.uuid4()),
                title="⚠️ Invalid Major Filter",
                description=".ceit 10, .mc 10, .me 10, .archi 10 စသဖြင့် ရှာပါ။",
                input_message_content=InputTextMessageContent(
                    "⚠️ <b>Invalid Major Filter.</b> Example: <code>.mc 10</code>",
                    parse_mode=ParseMode.HTML
                )
            )]
            return await update.inline_query.answer(result, cache_time=1, is_personal=True)
        return await update.inline_query.answer([], cache_time=1, is_personal=True)

    query = plan["query"]
    target_major = plan["target_major"]
    if not query:
        return await update.inline_query.answer([], cache_time=1, is_personal=True)

    or_clauses = []
    if plan["roll_number"] is not None:
        r_int = plan["roll_number"]
        or_clauses.extend([
            {"roll_number": r_int},
            {"roll_no": re.compile(rf"(?:^|-)0*{r_int}$", re.IGNORECASE)},
            {"roll_number_str": re.compile(rf"^0*{r_int}$", re.IGNORECASE)},
        ])

    regex = re.compile(re.escape(query), re.IGNORECASE)
    or_clauses.extend([
        {"roll_no": regex},
        {"roll_number_str": regex},
        {"legacy_roll_no": regex},
        {"name": regex},
        {"tg_username": regex},
    ])

    db = get_db(major=target_major) if target_major else get_db()
    if db is None:
        return await update.inline_query.answer([], cache_time=1, is_personal=True)

    if target_major:
        major_filter = build_major_filter(target_major)
    else:
        # Global search is Super Admin-only (enforced above). Records with no
        # major are included for legacy CEIT records; explicit legacy prefixes
        # can still be decoded by extract_major_from_roll below.
        major_filter = {"$or": [
            {"major": {"$in": list(VALID_MAJORS)}},
            {"major": {"$exists": False}},
        ]}

    student_query = {"$and": [major_filter, {"$or": or_clauses}]}
    students = await db.student_info.find(student_query).to_list(length=50)

    funds_cache: dict[str, list] = {}
    results = []

    for student in students:
        st_major = normalize_major(student.get("major")) or extract_major_from_roll(str(student.get("roll_no", ""))) or "ceit"
        # Defensive Major check for legacy records with no `major` field.
        if target_major and st_major != target_major:
            continue
        if st_major not in VALID_MAJORS:
            continue
        if st_major not in funds_cache:
            funds_cache[st_major] = await get_active_funds_for_major(st_major)

        name = str(student.get("name", "Unknown"))
        msg_text = build_student_message(student, funds_cache[st_major])
        shown_roll = get_short_roll(student) or str(student.get("roll_no", "Unknown"))
        semester = student.get("current_semester", "?")

        results.append(InlineQueryResultArticle(
            id=str(uuid.uuid4()),
            title=f"[{st_major.upper()} · Sem {semester}] {shown_roll} - {name}",
            description=f"Phone: {student.get('phone', 'No Phone')}",
            input_message_content=InputTextMessageContent(msg_text, parse_mode=ParseMode.HTML)
        ))

    await update.inline_query.answer(results[:50], cache_time=1, is_personal=True)


async def add_dummy_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_super_admin(user_id):
        return

    major = await get_user_major(user_id)
    try:
        sem_int = await get_current_semester(major, strict=True)
    except SemesterNotConfiguredError as e:
        return await update.message.reply_text(str(e), parse_mode=ParseMode.HTML)

    db = get_db(major=major)
    if db is None:
        return await update.message.reply_text("❌ Database ချိတ်ဆက်မှု မအောင်မြင်ပါ။")

    await db.funds.update_one(
        {"$and": [build_fund_major_filter(major), {"fund_id": "sports"}]},
        {"$set": {
            "major": major,
            "fund_id": "sports",
            "target_amount": 5000,
            "fund_name": "Sports Fund",
            "status": "active"
        }},
        upsert=True
    )

    for i in range(1, 6):
        await db.student_info.insert_one({
            "internal_id": f"stu_{uuid.uuid4().hex[:12]}",
            "major": major,
            "current_semester": sem_int,
            "roll_number": i,
            "roll_number_str": f"{i:02d}",
            "roll_no": f"{i:02d}",
            "name": f"Dummy {major.upper()} User {i}",
            "phone": f"0912345678{i}",
            "hostel": "Yes" if i % 2 == 0 else "No",
            "tg_username": f"dummy_{major}_{i}",
            "paid_funds": {"sports": 2000 * i}
        })

    await update.message.reply_text(
        f"✅ <b>[{major.upper()} | Sem {sem_int}]</b> အတွက် Realistic Dummy Data များ ထည့်သွင်းပြီးပါပြီ။",
        parse_mode=ParseMode.HTML
    )