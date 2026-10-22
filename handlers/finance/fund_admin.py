# handlers/finance/fund_admin.py

import html
import logging
import re
import secrets
from datetime import datetime, timezone, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from database.db import get_db
from utils.auth import is_super_admin
from services.major_service import get_user_major, is_major_authorized, normalize_major
from services.roll_number_service import RollNumberError, CrossMajorAccessError, MAJOR_ALIASES
from services.audit_service import log_audit, mm_timestamp
from services.finance_service import (
    build_major_filter,
    build_fund_major_filter,
    find_student_by_roll_in_major,
    record_student_payment,
    undo_student_payment,
    extract_paid_amount,
    get_short_roll,
    fund_is_current_semester,
    get_major_semester_context,
)

logger = logging.getLogger(__name__)


# ========================= INLINE FUND / BATCH PAYMENT FLOW =========================

_FLOW_KEY = "__plumber_fund_flow_v2"
_FLOW_PREFIX = "fundflow:"
_MAX_ROLLS_PER_BATCH = 100


def _ff_cancel_keyboard(back: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if back:
        rows.append([InlineKeyboardButton("⬅️ Back", callback_data=f"{_FLOW_PREFIX}back")])
    rows.append([InlineKeyboardButton("✖️ Cancel", callback_data=f"{_FLOW_PREFIX}cancel")])
    return InlineKeyboardMarkup(rows)


def _ff_html(value) -> str:
    return html.escape(str(value if value is not None else ""))


def _flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> dict | None:
    data = context.user_data.get(_FLOW_KEY)
    if not isinstance(data, dict) or data.get("actor_id") != update.effective_user.id:
        return None
    return data


async def _active_ecs_for_major(major: str) -> list[dict]:
    """Return active EC-role documents from the shared EC registry."""
    shared = get_db()
    if shared is None:
        return []
    rows = await shared.ec_roles.find({"major": major, "role": "ec"}).sort("nickname", 1).to_list(length=None)
    return [row for row in rows if row.get("active", True) is not False and row.get("user_id") and row.get("nickname")]


async def _show_newfund_mode(message, data: dict) -> None:
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 ကျောင်းသား allocation အတိုင်း", callback_data=f"{_FLOW_PREFIX}newmode:assigned")],
        [InlineKeyboardButton("👤 EC တစ်ယောက်က fund တာဝန်ယူမယ်", callback_data=f"{_FLOW_PREFIX}newmode:manager")],
        [InlineKeyboardButton("⬅️ Back", callback_data=f"{_FLOW_PREFIX}back"), InlineKeyboardButton("✖️ Cancel", callback_data=f"{_FLOW_PREFIX}cancel")],
    ])
    await message.reply_text(
        "Fund ကို ဘယ်လိုတာဝန်ခွဲမလဲ ရွေးပါ။\n\n"
        "• လစဉ်ကြေးလို ကျောင်းသားတွေကို allocation လုပ်ထားတဲ့ EC အလိုက်တွက်ရန်\n"
        "• TutorBook/Party လို EC တစ်ယောက်က fund တစ်ခုလုံးကို တာဝန်ယူရန်",
        reply_markup=keyboard,
    )


async def _list_pay_funds(message, context: ContextTypes.DEFAULT_TYPE, major: str, page: int = 0, edit: bool = False) -> None:
    db = get_db(major=major)
    if db is None:
        text = "❌ Database ချိတ်ဆက်မှု မရပါ။"
        if edit:
            await message.edit_text(text)
        else:
            await message.reply_text(text)
        return
    fund_filter = build_fund_major_filter(major)
    funds = await db.funds.find({"$and": [fund_filter, {"$or": [{"status": "active"}, {"status": {"$exists": False}}], "type": {"$ne": "opening_balance"}}]}).sort("fund_name", 1).to_list(length=None)
    current_semester, current_year = await get_major_semester_context(major)
    funds = [fund for fund in funds if fund_is_current_semester(fund, current_semester, current_year)]
    if not funds:
        context.user_data.pop(_FLOW_KEY, None)
        text = "📭 Active fund မရှိသေးပါ။ `/newfund` နဲ့ fund အသစ်ဖန်တီးပါ။"
        if edit:
            await message.edit_text(text, parse_mode=ParseMode.MARKDOWN)
        else:
            await message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        return

    page_size = 8
    page_count = max(1, (len(funds) + page_size - 1) // page_size)
    page = max(0, min(page, page_count - 1))
    shown = funds[page * page_size:(page + 1) * page_size]
    rows = []
    for fund in shown:
        fund_id = str(fund.get("fund_id", ""))
        name = str(fund.get("fund_name") or fund.get("title") or fund_id)
        amount = int(fund.get("target_amount", 0) or 0)
        label = f"{name[:35]} · {amount:,} MMK"
        rows.append([InlineKeyboardButton(label, callback_data=f"{_FLOW_PREFIX}payfund:{fund_id}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"{_FLOW_PREFIX}paypage:{page-1}"))
    if page + 1 < page_count:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"{_FLOW_PREFIX}paypage:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("✖️ Cancel", callback_data=f"{_FLOW_PREFIX}cancel")])
    text = f"💰 <b>[{major.upper()}] Payment အတွက် Fund ရွေးပါ</b>\nစာမျက်နှာ {page+1}/{page_count}\n\nFund တစ်ခုကို ရွေးပြီး roll number အများကြီးကို တစ်ခါတည်းထည့်နိုင်ပါတယ်။"
    keyboard = InlineKeyboardMarkup(rows)
    if edit:
        await message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    else:
        await message.reply_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


async def _fund_preview_text(data: dict) -> str:
    mode_label = "ကျောင်းသား allocation အတိုင်း EC တစ်ဦးချင်းစီ" if data.get("assignment_mode") == "student_allocation" else f"EC @{data.get('managed_by', 'ec')}"
    return (
        f"<b>Fund Confirmation</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"🏛 Major: <b>{data['major'].upper()}</b>\n"
        f"🏷️ Fund အမည်: <b>{_ff_html(data['fund_name'])}</b>\n"
        f"💵 ကျောင်းသားတစ်ဦးချင်း ပေးရမည့်ငွေ: <b>{int(data['target_amount']):,} MMK</b>\n"
        f"👤 တာဝန်ခွဲဝေပုံ: <b>{_ff_html(mode_label)}</b>\n\n"
        "Confirm လုပ်မှသာ Fund အသစ်ကို database ထဲ သိမ်းပါမယ်။"
    )


async def _save_new_fund(query, context: ContextTypes.DEFAULT_TYPE, data: dict) -> None:
    major = data["major"]
    db = get_db(major=major)
    if db is None:
        await query.edit_message_text("❌ Database ချိတ်ဆက်မှု မရပါ။")
        context.user_data.pop(_FLOW_KEY, None)
        return
    now = datetime.now(timezone(timedelta(hours=6, minutes=30)))
    fund_id = f"{major}-{now.strftime('%Y%m%d')}-{secrets.token_hex(4)}".lower()
    settings = await db.academic_settings.find_one({"major": major}) or {}
    semester = settings.get("current_semester") or settings.get("semester")
    academic_year = settings.get("academic_year")
    if not semester:
        semester = await db.student_info.find_one({"$and": [build_major_filter(major), {"current_semester": {"$exists": True}}]}, {"current_semester": 1})
        semester = semester.get("current_semester") if semester else None

    doc = {
        "major": major,
        "fund_id": fund_id,
        "fund_name": data["fund_name"],
        "target_amount": int(data["target_amount"]),
        "managed_by": data.get("managed_by", "ec"),
        "manager_user_id": data.get("manager_user_id"),
        "assignment_mode": data.get("assignment_mode", "student_allocation"),
        "semester": semester,
        "academic_year": academic_year,
        "semester_key": f"{academic_year or 'unknown'}-S{semester}" if semester is not None else None,
        "status": "active",
        "created_by": data["actor_id"],
        "created_at": now.strftime("%Y-%m-%d %H:%M:%S"),
    }
    await db.funds.insert_one(doc)
    try:
        await log_audit(db, major=major, actor_id=data["actor_id"], action="fund_created",
                        entity_type="fund", entity_id=fund_id, target_name=data["fund_name"],
                        details={"amount": int(data["target_amount"]), "assignment_mode": doc["assignment_mode"],
                                 "managed_by": doc.get("managed_by")}, semester=semester)
    except Exception:
        logger.exception("Audit log failed for fund creation")
    context.user_data.pop(_FLOW_KEY, None)
    await query.edit_message_text(
        f"✅ <b>Fund ဖန်တီးပြီးပါပြီ။</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"🏛 Major: <b>{major.upper()}</b>\n"
        f"🏷️ Fund: <b>{_ff_html(data['fund_name'])}</b>\n"
        f"🆔 Fund ID: <code>{fund_id}</code>\n"
        f"💵 တစ်ဦးချင်း ပေးရမည့်ငွေ: <b>{int(data['target_amount']):,} MMK</b>\n"
        f"👤 တာဝန်ခံ: <b>{_ff_html(data.get('managed_by', 'ec'))}</b>\n\n"
        "`/pay` မှာ Fund ID ကို ကိုယ်တိုင်ရိုက်စရာမလိုဘဲ ဒီ Fund ကို ရွေးနိုင်ပါပြီ။",
        parse_mode=ParseMode.HTML,
    )


async def newfund_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    major = normalize_major(await get_user_major(user_id))
    if not major or not await is_major_authorized(user_id, major):
        return await update.message.reply_text("⛔ သက်ဆိုင်ရာ Major ရဲ့ EC/Admin များသာ ဒီ command ကို သုံးနိုင်ပါတယ်။")
    context.user_data.pop(_FUND_MANAGE_KEY, None)
    context.user_data[_FLOW_KEY] = {
        "type": "newfund", "step": "name", "actor_id": user_id, "major": major,
    }
    await update.message.reply_text(
        f"🆕 <b>[{major.upper()}] Fund အသစ်ဖန်တီးမယ်</b>\n\nFund အမည်ကို ထည့်ပါ။\nဥပမာ — လစဉ်ကြေး၊ TutorBook၊ Party ကြေး",
        parse_mode=ParseMode.HTML,
        reply_markup=_ff_cancel_keyboard(),
    )


async def payfund_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    major = normalize_major(await get_user_major(user_id))
    if not major or not await is_major_authorized(user_id, major):
        return await update.message.reply_text("⛔ သက်ဆိုင်ရာ Major ရဲ့ EC/Admin များသာ ဒီ command ကို သုံးနိုင်ပါတယ်။")
    context.user_data.pop(_FUND_MANAGE_KEY, None)
    context.user_data[_FLOW_KEY] = {
        "type": "pay", "step": "select_fund", "actor_id": user_id, "major": major,
    }
    await _list_pay_funds(update.message, context, major)


def _amount_of_student_fund(student: dict, fund_id: str) -> int:
    return extract_paid_amount((student.get("paid_funds") or {}).get(fund_id, 0))


def _normalise_batch_rolls(raw: str) -> tuple[list[str], list[str], list[str]]:
    """Normalize short rolls and separately report invalid and duplicate input tokens."""
    tokens = raw.split()
    invalid, duplicates, rolls, seen = [], [], [], set()
    for token in tokens:
        if not re.fullmatch(r"\d{1,3}", token):
            invalid.append(token)
            continue
        number = int(token)
        if number < 1 or number > 999:
            invalid.append(token)
            continue
        short = f"{number:02d}" if number < 100 else str(number)
        if short in seen:
            duplicates.append(short)
            continue
        rolls.append(short)
        seen.add(short)
    return rolls, invalid, duplicates


def _chunk_lines(lines: list[str], max_chars: int = 3300) -> list[str]:
    chunks, current = [], ""
    for line in lines:
        if current and len(current) + len(line) + 1 > max_chars:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


async def _show_pay_confirmation(query, context: ContextTypes.DEFAULT_TYPE, data: dict) -> None:
    eligible = data.get("eligible_students", [])
    amount = int(data.get("amount", 0))
    if not eligible:
        await query.edit_message_text("⚠️ Payment လုပ်နိုင်တဲ့ ကျောင်းသား မကျန်တော့ပါ။ ပြန်စရန် /pay ကို သုံးပါ။")
        context.user_data.pop(_FLOW_KEY, None)
        return
    lines = [f"<b>Payment Confirmation</b>", "━━━━━━━━━━━━━━━━━━"]
    lines.append(f"🏛 Major: <b>{data['major'].upper()}</b>")
    lines.append(f"💰 Fund: <b>{_ff_html(data['fund_name'])}</b>")
    lines.append(f"👤 လက်ခံသူ EC: <b>{_ff_html(data.get('receiver_name') or 'သင်ကိုယ်တိုင်')}</b>")
    lines.append(f"👥 ကျောင်းသား: <b>{len(eligible)}</b> ယောက်")
    lines.append(f"💵 တစ်ယောက်လျှင်: <b>{amount:,} MMK</b>")
    lines.append(f"🧮 စုစုပေါင်း: <b>{amount * len(eligible):,} MMK</b>")
    lines.append("")
    for st in eligible:
        lines.append(f"• <code>{_ff_html(st['roll'])}</code> — {_ff_html(st['name'])}")
    skipped = data.get("skipped", [])
    if skipped:
        lines.extend(["", "<b>⚠️ ကျော်ထားသော roll များ</b>"])
        for item in skipped[:50]:
            lines.append(f"• <code>{_ff_html(item['roll'])}</code> — {_ff_html(item['reason'])}")
        if len(skipped) > 50:
            lines.append(f"… အခြား {len(skipped) - 50} ခု")
    text = "\n".join(lines)
    # Usually one message is enough; split the student list if it approaches Telegram's limit.
    if len(text) > 3900:
        head = "\n".join(lines[:8])
        student_lines = [f"• <code>{_ff_html(st['roll'])}</code> — {_ff_html(st['name'])}" for st in eligible]
        chunks = _chunk_lines(student_lines)
        await query.edit_message_text(head + "\n\n<b>ကျောင်းသားစာရင်း</b> (အောက်တွင် ဆက်လက်ဖော်ပြထားသည်)", parse_mode=ParseMode.HTML)
        for chunk in chunks:
            await context.bot.send_message(chat_id=query.message.chat.id, text=chunk, parse_mode=ParseMode.HTML)
        if skipped:
            skip_lines = [f"• <code>{_ff_html(x['roll'])}</code> — {_ff_html(x['reason'])}" for x in skipped]
            for chunk in _chunk_lines(["<b>ကျော်ထားသော roll များ</b>"] + skip_lines):
                await context.bot.send_message(chat_id=query.message.chat.id, text=chunk, parse_mode=ParseMode.HTML)
        await context.bot.send_message(chat_id=query.message.chat.id, text="အထက်ပါစာရင်းကို စစ်ပြီးနောက် အောက်တွင် Confirm လုပ်ပါ။", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Confirm Payment", callback_data=f"{_FLOW_PREFIX}confirmpay")],
            [InlineKeyboardButton("⬅️ Back", callback_data=f"{_FLOW_PREFIX}back"), InlineKeyboardButton("✖️ Cancel", callback_data=f"{_FLOW_PREFIX}cancel")],
        ]))
    else:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Confirm Payment", callback_data=f"{_FLOW_PREFIX}confirmpay")],
            [InlineKeyboardButton("⬅️ Back", callback_data=f"{_FLOW_PREFIX}back"), InlineKeyboardButton("✖️ Cancel", callback_data=f"{_FLOW_PREFIX}cancel")],
        ]), parse_mode=ParseMode.HTML)
    data["step"] = "confirm_payment"


async def fund_flow_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    manage_state = context.user_data.get(_FUND_MANAGE_KEY)
    if isinstance(manage_state, dict) and manage_state.get("input_step"):
        return await fund_manage_text_handler(update, context)
    data = _flow(update, context)
    if not data:
        return
    text = update.message.text.strip()
    major = data["major"]
    if not await is_major_authorized(update.effective_user.id, major):
        context.user_data.pop(_FLOW_KEY, None)
        return await update.message.reply_text("⛔ Permission မရှိတော့ပါ။ Flow ကို ပိတ်လိုက်ပါတယ်။")

    if data.get("type") == "newfund":
        step = data.get("step")
        if step == "name":
            if not text or len(text) > 80:
                return await update.message.reply_text("Fund အမည်ကို ၁ မှ ၈၀ လုံးအတွင်း ထည့်ပါ။", reply_markup=_ff_cancel_keyboard())
            data["fund_name"] = text
            data["step"] = "amount"
            return await update.message.reply_text(
                "ကျောင်းသားတစ်ယောက်ချင်း ပေးရမယ့် Fund amount ကို MMK ဂဏန်းနဲ့ထည့်ပါ။\nဥပမာ — <code>5000</code>",
                parse_mode=ParseMode.HTML, reply_markup=_ff_cancel_keyboard(back=True),
            )
        if step == "amount":
            if not text.isdigit() or not (1 <= int(text) <= 1_000_000_000):
                return await update.message.reply_text("❌ ပမာဏကို ၁ ထက်ကြီးတဲ့ ဂဏန်းနဲ့ထည့်ပါ။", reply_markup=_ff_cancel_keyboard(back=True))
            data["target_amount"] = int(text)
            data["step"] = "mode"
            return await _show_newfund_mode(update.message, data)

    if data.get("type") == "pay" and data.get("step") == "rolls":
        rolls, invalid, duplicates = _normalise_batch_rolls(text)
        if not rolls:
            problem = f"\nမမှန်တဲ့ roll: {_ff_html(', '.join(invalid[:20]))}" if invalid else ""
            return await update.message.reply_text("Roll number အတိုတွေကို space ခြားပြီး ထည့်ပါ။ ဥပမာ <code>01 23 04 24 48</code>" + problem,
                parse_mode=ParseMode.HTML, reply_markup=_ff_cancel_keyboard(back=True))
        if len(rolls) > _MAX_ROLLS_PER_BATCH:
            return await update.message.reply_text(f"တစ်ခါလျှင် {_MAX_ROLLS_PER_BATCH} roll အထိသာ ထည့်နိုင်ပါတယ်။", reply_markup=_ff_cancel_keyboard(back=True))

        db = get_db(major=major)
        fund = await db.funds.find_one({"$and": [build_fund_major_filter(major), {"fund_id": data["fund_id"]}]}) if db is not None else None
        if not fund or fund.get("status", "active") != "active":
            context.user_data.pop(_FLOW_KEY, None)
            return await update.message.reply_text("⚠️ ဒီ Fund က Active မဖြစ်တော့ပါ။ `/pay` မှာ Fund အသစ်ရွေးပါ။")
        eligible, skipped = [], [{"roll": r, "reason": "မမှန်သော roll format"} for r in invalid]
        skipped.extend({"roll": r, "reason": "Input ထဲတွင် ထပ်နေသောကြောင့် တစ်ကြိမ်သာယူသည်"} for r in duplicates)
        for roll in rolls:
            try:
                student = await find_student_by_roll_in_major(roll, major)
            except (RollNumberError, CrossMajorAccessError):
                student = None
            if not student:
                skipped.append({"roll": roll, "reason": "ကျောင်းသား မတွေ့ပါ"})
                continue
            existing = _amount_of_student_fund(student, data["fund_id"])
            if existing > 0:
                skipped.append({"roll": roll, "reason": f"အရင်က {existing:,} MMK မှတ်တမ်းရှိပြီးသား"})
                continue
            eligible.append({"roll": get_short_roll(student), "query_roll": roll, "name": student.get("name") or "Unknown"})
        data.update({"eligible_students": eligible, "skipped": skipped, "fund_name": fund.get("fund_name") or fund.get("title") or data["fund_id"], "target_amount": int(fund.get("target_amount", 0) or 0)})
        data["step"] = "amount_choice"
        amount = data["target_amount"]
        rows = []
        if amount > 0:
            rows.append([InlineKeyboardButton(f"✅ Fund amount ({amount:,} MMK)", callback_data=f"{_FLOW_PREFIX}defaultamount")])
        rows.append([InlineKeyboardButton("✏️ Amount ကိုယ်တိုင်ထည့်မယ်", callback_data=f"{_FLOW_PREFIX}customamount")])
        rows.append([InlineKeyboardButton("⬅️ Back", callback_data=f"{_FLOW_PREFIX}back"), InlineKeyboardButton("✖️ Cancel", callback_data=f"{_FLOW_PREFIX}cancel")])
        result = f"✅ စစ်ဆေးပြီးပါပြီ။\n\nသိမ်းနိုင်မယ့် ကျောင်းသား: <b>{len(eligible)}</b> ယောက်\nကျော်ထားမယ့် Roll: <b>{len(skipped)}</b> ခု\n\nအားလုံးအတွက် amount တူညီပါမယ်။ Amount ရွေးပါ။"
        if not eligible:
            context.user_data.pop(_FLOW_KEY, None)
            details = "\n".join(f"• {x['roll']}: {x['reason']}" for x in skipped[:25])
            return await update.message.reply_text("⚠️ Payment လုပ်နိုင်သူ မရှိပါ။\n" + details + "\n/pay နဲ့ ပြန်စပါ။")
        return await update.message.reply_text(result, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))

    if data.get("type") == "pay" and data.get("step") == "custom_amount":
        if not text.isdigit() or not (1 <= int(text) <= 1_000_000_000):
            return await update.message.reply_text("❌ Amount ကို ၁ ထက်ကြီးတဲ့ ဂဏန်းနဲ့ထည့်ပါ။", reply_markup=_ff_cancel_keyboard(back=True))
        data["amount"] = int(text)
        db = get_db(major=major)
        fund = await db.funds.find_one({"$and": [build_fund_major_filter(major), {"fund_id": data["fund_id"]}]}) if db is not None else None
        if not fund or fund.get("status", "active") != "active":
            context.user_data.pop(_FLOW_KEY, None)
            return await update.message.reply_text("⚠️ ဒီ Fund က Active မဖြစ်တော့ပါ။ `/pay` မှာ ပြန်ရွေးပါ။")
        data["fund_name"] = fund.get("fund_name") or fund.get("title") or data["fund_id"]
        data["receiver_name"] = update.effective_user.username or update.effective_user.first_name
        data["step"] = "amount_choice"
        class _FakeQuery:
            message = update.message
            async def edit_message_text(self, *args, **kwargs):
                return await update.message.reply_text(*args, **kwargs)
        return await _show_pay_confirmation(_FakeQuery(), context, data)


async def fund_flow_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    payload = query.data[len(_FLOW_PREFIX):]
    data = _flow(update, context)

    if payload == "cancel":
        if not data:
            return await query.edit_message_text("ဒီ action သက်တမ်းကုန်သွားပါပြီ။")
        context.user_data.pop(_FLOW_KEY, None)
        return await query.edit_message_text("✖️ လုပ်ဆောင်ချက်ကို Cancel လုပ်လိုက်ပါပြီ။")
    if not data:
        return await query.edit_message_text("ဒီ action သက်တမ်းကုန်သွားပါပြီ။ `/newfund` သို့မဟုတ် `/pay` နဲ့ ပြန်စပါ။")
    major = data["major"]
    if not await is_major_authorized(update.effective_user.id, major):
        context.user_data.pop(_FLOW_KEY, None)
        return await query.edit_message_text("⛔ ဒီ Major အတွက် ခွင့်ပြုချက် မရှိပါ။")

    if payload == "back":
        if data.get("type") == "newfund":
            step = data.get("step")
            if step == "amount":
                data["step"] = "name"
                return await query.edit_message_text("Fund အမည်ကို ထည့်ပါ။", reply_markup=_ff_cancel_keyboard())
            if step == "mode":
                data["step"] = "amount"
                return await query.edit_message_text("ကျောင်းသားတစ်ယောက်ချင်း ပေးရမယ့် amount ကို MMK ဂဏန်းနဲ့ထည့်ပါ။", reply_markup=_ff_cancel_keyboard(back=True))
            if step == "manager" or (step == "confirm" and data.get("assignment_mode") != "single_manager"):
                data["step"] = "mode"
                return await query.edit_message_text("Fund တာဝန်ခွဲဝေပုံကို ရွေးပါ။", reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("👥 ကျောင်းသား allocation အတိုင်း", callback_data=f"{_FLOW_PREFIX}newmode:assigned")],
                    [InlineKeyboardButton("👤 EC တစ်ယောက်", callback_data=f"{_FLOW_PREFIX}newmode:manager")],
                    [InlineKeyboardButton("⬅️ Back", callback_data=f"{_FLOW_PREFIX}back"), InlineKeyboardButton("✖️ Cancel", callback_data=f"{_FLOW_PREFIX}cancel")],
                ]))
            if step == "confirm" and data.get("assignment_mode") == "single_manager":
                ecs = await _active_ecs_for_major(major)
                data["step"] = "manager"
                rows = [[InlineKeyboardButton(f"@{str(ec['nickname'])[:25]}", callback_data=f"{_FLOW_PREFIX}manager:{int(ec['user_id'])}")] for ec in ecs]
                rows.append([InlineKeyboardButton("⬅️ Back", callback_data=f"{_FLOW_PREFIX}back"), InlineKeyboardButton("✖️ Cancel", callback_data=f"{_FLOW_PREFIX}cancel")])
                return await query.edit_message_text(f"[{major.upper()}] Fund တာဝန်ခံ EC ကို ရွေးပါ။", reply_markup=InlineKeyboardMarkup(rows))
        elif data.get("type") == "pay":
            step = data.get("step")
            if step in ("rolls", "amount_choice", "custom_amount", "confirm_payment"):
                if step == "rolls":
                    data["step"] = "select_fund"
                    return await _list_pay_funds(query.message, context, major, edit=True)
                if step == "amount_choice":
                    data["step"] = "rolls"
                    return await query.edit_message_text(
                        f"💰 <b>{_ff_html(data.get('fund_name', data.get('fund_id')))}</b> အတွက် roll number အတိုတွေကို space ခြားပြီး ထည့်ပါ။\nဥပမာ <code>01 23 04 24 48</code>",
                        parse_mode=ParseMode.HTML, reply_markup=_ff_cancel_keyboard(back=True),
                    )
                if step == "custom_amount":
                    data["step"] = "amount_choice"
                    return await query.edit_message_text("Amount ရွေးပါ။", reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton(f"✅ Fund amount ({data.get('target_amount', 0):,} MMK)", callback_data=f"{_FLOW_PREFIX}defaultamount")],
                        [InlineKeyboardButton("✏️ Amount ကိုယ်တိုင်ထည့်မယ်", callback_data=f"{_FLOW_PREFIX}customamount")],
                        [InlineKeyboardButton("⬅️ Back", callback_data=f"{_FLOW_PREFIX}back"), InlineKeyboardButton("✖️ Cancel", callback_data=f"{_FLOW_PREFIX}cancel")],
                    ]))
                if step == "confirm_payment":
                    data["step"] = "amount_choice"
                    return await query.edit_message_text("Amount ရွေးပါ။", reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton(f"✅ Fund amount ({data.get('target_amount', 0):,} MMK)", callback_data=f"{_FLOW_PREFIX}defaultamount")],
                        [InlineKeyboardButton("✏️ Amount ကိုယ်တိုင်ထည့်မယ်", callback_data=f"{_FLOW_PREFIX}customamount")],
                        [InlineKeyboardButton("✖️ Cancel", callback_data=f"{_FLOW_PREFIX}cancel")],
                    ]))
        return await query.edit_message_text("ဒီနေရာကနေ Back ပြန်မသွားနိုင်ပါ။ Cancel လုပ်ပြီး ပြန်စပါ။", reply_markup=_ff_cancel_keyboard())

    if payload.startswith("paypage:"):
        if data.get("type") != "pay" or data.get("step") != "select_fund":
            return await query.edit_message_text("ဒီစာရင်း သက်တမ်းကုန်သွားပါပြီ။")
        try:
            page = int(payload.split(":", 1)[1])
        except ValueError:
            page = 0
        return await _list_pay_funds(query.message, context, major, page, edit=True)

    if payload.startswith("payfund:"):
        if data.get("type") != "pay" or data.get("step") != "select_fund":
            return await query.edit_message_text("ဒီ Fund ရွေးချယ်မှု သက်တမ်းကုန်သွားပါပြီ။")
        fund_id = payload.split(":", 1)[1]
        db = get_db(major=major)
        fund = await db.funds.find_one({"$and": [build_fund_major_filter(major), {"fund_id": fund_id}]}) if db is not None else None
        if not fund or fund.get("status", "active") != "active":
            return await query.edit_message_text("⚠️ ဒီ Fund ကို မတွေ့ပါ သို့မဟုတ် Active မဖြစ်တော့ပါ။ `/pay` နဲ့ ပြန်စပါ။")
        data.update({"fund_id": fund_id, "fund_name": fund.get("fund_name") or fund.get("title") or fund_id, "target_amount": int(fund.get("target_amount", 0) or 0), "step": "rolls"})
        return await query.edit_message_text(
            f"💰 <b>{_ff_html(data['fund_name'])}</b> ရွေးပြီးပါပြီ။\nတစ်ယောက်ချင်း ပေးရမည့်ငွေ: <b>{data['target_amount']:,} MMK</b>\n\nRoll number အတိုတွေကို space ခြားပြီး ထည့်ပါ။\nဥပမာ <code>01 23 04 24 48</code>",
            parse_mode=ParseMode.HTML, reply_markup=_ff_cancel_keyboard(back=True),
        )

    if payload.startswith("newmode:"):
        if data.get("type") != "newfund" or data.get("step") != "mode":
            return await query.edit_message_text("ဒီအဆင့် သက်တမ်းကုန်သွားပါပြီ။")
        mode = payload.split(":", 1)[1]
        if mode == "assigned":
            data.update({"assignment_mode": "student_allocation", "managed_by": "ec", "manager_user_id": None, "step": "confirm"})
            text = await _fund_preview_text(data)
            return await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Confirm Fund", callback_data=f"{_FLOW_PREFIX}confirmfund")],
                [InlineKeyboardButton("⬅️ Back", callback_data=f"{_FLOW_PREFIX}back"), InlineKeyboardButton("✖️ Cancel", callback_data=f"{_FLOW_PREFIX}cancel")],
            ]))
        if mode == "manager":
            ecs = await _active_ecs_for_major(major)
            if not ecs:
                return await query.edit_message_text("⚠️ ဒီ Major အတွက် active EC registry မရှိပါ။ Super Admin က /addec နဲ့ EC တွေထည့်ပြီး ပြန်စမ်းပါ။", reply_markup=_ff_cancel_keyboard(back=True))
            data["step"] = "manager"
            rows = [[InlineKeyboardButton(f"@{str(ec['nickname'])[:25]}", callback_data=f"{_FLOW_PREFIX}manager:{int(ec['user_id'])}")] for ec in ecs]
            rows.append([InlineKeyboardButton("⬅️ Back", callback_data=f"{_FLOW_PREFIX}back"), InlineKeyboardButton("✖️ Cancel", callback_data=f"{_FLOW_PREFIX}cancel")])
            return await query.edit_message_text(f"[{major.upper()}] Fund တာဝန်ခံ EC ကို ရွေးပါ။", reply_markup=InlineKeyboardMarkup(rows))

    if payload.startswith("manager:"):
        if data.get("type") != "newfund" or data.get("step") != "manager":
            return await query.edit_message_text("ဒီ EC ရွေးချယ်မှု သက်တမ်းကုန်သွားပါပြီ။")
        try:
            manager_id = int(payload.split(":", 1)[1])
        except ValueError:
            return await query.edit_message_text("EC ID မမှန်ပါ။")
        ecs = await _active_ecs_for_major(major)
        manager = next((ec for ec in ecs if int(ec.get("user_id", 0)) == manager_id), None)
        if not manager:
            return await query.edit_message_text("⚠️ ဒီ EC က active မဟုတ်တော့ပါ။ Fund flow ကို ပြန်စပါ။")
        data.update({"assignment_mode": "single_manager", "managed_by": str(manager.get("nickname")).lower(), "manager_user_id": manager_id, "step": "confirm"})
        return await query.edit_message_text(await _fund_preview_text(data), parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Confirm Fund", callback_data=f"{_FLOW_PREFIX}confirmfund")],
            [InlineKeyboardButton("⬅️ Back", callback_data=f"{_FLOW_PREFIX}back"), InlineKeyboardButton("✖️ Cancel", callback_data=f"{_FLOW_PREFIX}cancel")],
        ]))

    if payload == "confirmfund":
        if data.get("type") != "newfund" or data.get("step") != "confirm":
            return await query.edit_message_text("ဒီ confirmation သက်တမ်းကုန်သွားပါပြီ။")
        data["step"] = "saving"
        return await _save_new_fund(query, context, data)

    if payload == "defaultamount":
        if data.get("type") != "pay" or data.get("step") != "amount_choice":
            return await query.edit_message_text("ဒီ amount ရွေးချယ်မှု သက်တမ်းကုန်သွားပါပြီ။")
        data["amount"] = int(data.get("target_amount", 0))
        if data["amount"] <= 0:
            return await query.edit_message_text("⚠️ Fund amount မမှန်ပါ။ Amount ကို ကိုယ်တိုင်ထည့်ပါ။", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✏️ Amount ထည့်မယ်", callback_data=f"{_FLOW_PREFIX}customamount")],
                [InlineKeyboardButton("✖️ Cancel", callback_data=f"{_FLOW_PREFIX}cancel")],
            ]))
        db = get_db(major=major)
        fund = await db.funds.find_one({"$and": [build_fund_major_filter(major), {"fund_id": data["fund_id"]}]}) if db is not None else None
        data["receiver_name"] = update.effective_user.username or update.effective_user.first_name
        if not fund or fund.get("status", "active") != "active":
            context.user_data.pop(_FLOW_KEY, None)
            return await query.edit_message_text("⚠️ Fund က Active မဖြစ်တော့ပါ။")
        data["step"] = "confirm_payment"
        return await _show_pay_confirmation(query, context, data)

    if payload == "customamount":
        if data.get("type") != "pay" or data.get("step") != "amount_choice":
            return await query.edit_message_text("ဒီ amount ရွေးချယ်မှု သက်တမ်းကုန်သွားပါပြီ။")
        data["step"] = "custom_amount"
        return await query.edit_message_text("ကျောင်းသားအားလုံးအတွက် တူညီမယ့် amount ကို MMK ဂဏန်းနဲ့ ထည့်ပါ။", reply_markup=_ff_cancel_keyboard(back=True))

    if payload == "confirmpay":
        if data.get("type") != "pay" or data.get("step") != "confirm_payment":
            return await query.edit_message_text("ဒီ confirmation ကို အသုံးပြုပြီးပါပြီ သို့မဟုတ် သက်တမ်းကုန်သွားပါပြီ။")
        # Lock this confirmation before doing any writes; repeat taps will be rejected.
        data["step"] = "processing"
        await query.edit_message_reply_markup(reply_markup=None)
        db = get_db(major=major)
        fund = await db.funds.find_one({"$and": [build_fund_major_filter(major), {"fund_id": data["fund_id"]}]}) if db is not None else None
        if not fund or fund.get("status", "active") != "active":
            context.user_data.pop(_FLOW_KEY, None)
            return await query.edit_message_text("⚠️ Fund က Active မဖြစ်တော့တဲ့အတွက် payment မသိမ်းခဲ့ပါ။")
        amount = int(data["amount"])
        admin_name = update.effective_user.username or update.effective_user.first_name or str(update.effective_user.id)
        successes, failed = [], []
        for item in data.get("eligible_students", []):
            try:
                student = await find_student_by_roll_in_major(item["query_roll"], major)
            except (RollNumberError, CrossMajorAccessError):
                student = None
            if not student:
                failed.append((item["roll"], "ကျောင်းသားကို ပြန်ရှာမတွေ့ပါ"))
                continue
            existing = _amount_of_student_fund(student, data["fund_id"])
            if existing > 0:
                failed.append((item["roll"], "confirmation မတိုင်ခင် payment ရှိလာသောကြောင့် ကျော်ထားသည်"))
                continue
            try:
                ok, err_msg, info = await record_student_payment(
                    roll_query=item["query_roll"], fund_id=data["fund_id"], amount=amount,
                    admin_id=update.effective_user.id, admin_name=admin_name, major=major,
                    allow_cross_major=False,
                    responsible_ec_id=(fund.get("manager_user_id") if fund.get("assignment_mode") == "single_manager" else student.get("assigned_ec_id")),
                    responsible_ec_name=(fund.get("managed_by") if fund.get("assignment_mode") == "single_manager" else (student.get("assigned_ec") or "unassigned")),
                    fund_semester=fund.get("semester"),
                )
            except Exception:
                logger.exception("Batch payment write failed for major=%s fund=%s roll=%s", major, data["fund_id"], item["roll"])
                failed.append((item["roll"], "Database မှတ်တမ်းသိမ်းရာတွင် error ဖြစ်သည်။ အောင်မြင်ပြီးသားစာရင်းများကို မဖျက်ထားပါ။"))
                continue
            if ok and info:
                successes.append(f"• <code>{_ff_html(info.get('roll_no') or item['roll'])}</code> — {_ff_html(info.get('name'))}: {amount:,} MMK")
            else:
                failed.append((item["roll"], re.sub(r"<[^>]+>", "", str(err_msg or "မအောင်မြင်ပါ"))))
        try:
            await log_audit(
                db, major=major, actor_id=update.effective_user.id, action="batch_payment",
                entity_type="fund", entity_id=data.get("fund_id"), target_name=data.get("fund_name"),
                details={"success_count": len(successes), "failed_count": len(failed),
                         "amount_each": amount, "total_amount": len(successes) * amount,
                         "received_by": update.effective_user.id}, semester=fund.get("semester")
            )
        except Exception:
            logger.exception("Audit log failed for batch payment")
        context.user_data.pop(_FLOW_KEY, None)
        total = len(successes) * amount
        summary = ["✅ <b>Batch Payment ပြီးပါပြီ</b>", "━━━━━━━━━━━━━━━━━━", f"Fund: <b>{_ff_html(data.get('fund_name', data['fund_id']))}</b>", f"မှတ်တမ်းတင်ပြီး: <b>{len(successes)}</b> ယောက်", f"စုစုပေါင်း: <b>{total:,} MMK</b>", "", "<b>အောင်မြင်သောမှတ်တမ်းများ</b>"] + successes
        if failed:
            summary.extend(["", "<b>⚠️ မသိမ်းနိုင်/ကျော်ထားသော Roll များ</b>"] + [f"• <code>{_ff_html(roll)}</code> — {_ff_html(reason)}" for roll, reason in failed])
        chunks = _chunk_lines(summary, 3500)
        await query.edit_message_text(chunks[0], parse_mode=ParseMode.HTML)
        for chunk in chunks[1:]:
            await context.bot.send_message(chat_id=query.message.chat.id, text=chunk, parse_mode=ParseMode.HTML)
        return

    return await query.edit_message_text("ဒီ button ကို နားမလည်ပါ။ Command ကို ပြန်စပါ။")


async def undo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    major = await get_user_major(user_id)
    if not await is_major_authorized(user_id, major):
        return

    if len(context.args) < 2:
        return await update.message.reply_text(
            "⚠️ <b>အသုံးပြုနည်း မှားယွင်းနေပါသည်။</b>\n<pre>/undo &lt;RollNo&gt; &lt;FundID&gt;</pre>\n(ဥပမာ - <code>/undo 01 sports</code>)",
            parse_mode=ParseMode.HTML
        )

    roll_query = context.args[0]
    fund_id = context.args[1].lower()

    expected_undo = context.user_data.pop("__plumber_undo_confirmation", None)
    expected_kwargs = {}
    if isinstance(expected_undo, dict):
        # Only honor callback state tied to this exact user, major, roll and fund.
        if (
            int(expected_undo.get("actor_id", 0)) == int(user_id)
            and str(expected_undo.get("major") or "") == str(major)
            and str(expected_undo.get("roll_query") or "").casefold() == str(roll_query).casefold()
            and str(expected_undo.get("fund_id") or "").casefold() == str(fund_id).casefold()
        ):
            expected_kwargs = {
                "expected_transaction_id": expected_undo.get("transaction_id") or None,
                "expected_transaction_fingerprint": expected_undo.get("transaction_fingerprint") or None,
                "expected_date": expected_undo.get("date"),
                "expected_amount": expected_undo.get("amount"),
            }

    ok, err_msg, info = await undo_student_payment(
        roll_query,
        fund_id,
        major=major,
        allow_cross_major=is_super_admin(user_id),
        reversed_by_id=user_id,
        **expected_kwargs,
    )
    if not ok:
        return await update.message.reply_text(err_msg, parse_mode=ParseMode.HTML)

    try:
        db = get_db(major=info.get("major", major).lower())
        await log_audit(db, major=info.get("major", major).lower(), actor_id=user_id,
                        action="payment_undo", entity_type="student_payment",
                        entity_id=f"{info.get('roll_no')}:{fund_id}", target_name=info.get("name"),
                        details={"reverted_amount": info.get("revert_amount", 0)},
                        semester=None)
    except Exception:
        logger.exception("Audit log failed for payment undo")

    await update.message.reply_text(
        f"⏪ <b>[{info['major']}] လုပ်ဆောင်ချက် ပြန်လည်ရုပ်သိမ်းပြီးပါပြီ (Undo)</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"👤 ကျောင်းသား: {info['name']} (<code>{info['roll_no']}</code>)\n"
        f"🗑 ဖျက်လိုက်သော သွင်းငွေ: <b>{info['revert_amount']:,} MMK</b> <i>({info['tx_date']})</i>\n"
        f"💰 ယခု ကျန်ရှိငွေ: <b>{info['new_paid']:,} MMK</b>",
        parse_mode=ParseMode.HTML
    )


async def setphone_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    major = await get_user_major(user_id)
    if not await is_major_authorized(user_id, major):
        return
    if len(context.args) < 2:
        return await update.message.reply_text("⚠️ <b>အသုံးပြုနည်း:</b> <code>/setphone &lt;RollNo&gt; &lt;Phone&gt;</code>", parse_mode=ParseMode.HTML)

    roll_query, phone = context.args[0], context.args[1]
    try:
        student = await find_student_by_roll_in_major(roll_query, major, allow_cross_major=is_super_admin(user_id))
    except (RollNumberError, CrossMajorAccessError) as e:
        return await update.message.reply_text(f"⚠️ {e}", parse_mode=ParseMode.HTML)

    if not student:
        return await update.message.reply_text(f"❌ <b>[{major.upper()}]</b> တွင် ကျောင်းသား ရှာမတွေ့ပါ။", parse_mode=ParseMode.HTML)

    student_major = normalize_major(student.get("major")) or normalize_major(major) or major
    db = get_db(major=student_major)
    if db is None:
        return await update.message.reply_text("❌ Database ချိတ်ဆက်မှု မအောင်မြင်ပါ။")
    await db.student_info.update_one({"_id": student["_id"]}, {"$set": {"phone": phone}})
    await update.message.reply_text(f"✅ <b>{student.get('name')}</b> (<code>{student.get('roll_no')}</code>) ၏ ဖုန်းနံပါတ် ပြင်ဆင်ပြီးပါပြီ။", parse_mode=ParseMode.HTML)


async def settg_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    major = await get_user_major(user_id)
    if not await is_major_authorized(user_id, major):
        return
    if len(context.args) < 2:
        return await update.message.reply_text("⚠️ <b>အသုံးပြုနည်း:</b> <code>/settg &lt;RollNo&gt; &lt;Username&gt;</code>", parse_mode=ParseMode.HTML)

    roll_query = context.args[0]
    username = context.args[1].replace("@", "")
    try:
        student = await find_student_by_roll_in_major(roll_query, major, allow_cross_major=is_super_admin(user_id))
    except (RollNumberError, CrossMajorAccessError) as e:
        return await update.message.reply_text(f"⚠️ {e}", parse_mode=ParseMode.HTML)

    if not student:
        return await update.message.reply_text(f"❌ <b>[{major.upper()}]</b> တွင် ကျောင်းသား ရှာမတွေ့ပါ။", parse_mode=ParseMode.HTML)

    student_major = normalize_major(student.get("major")) or normalize_major(major) or major
    db = get_db(major=student_major)
    if db is None:
        return await update.message.reply_text("❌ Database ချိတ်ဆက်မှု မအောင်မြင်ပါ။")
    await db.student_info.update_one({"_id": student["_id"]}, {"$set": {"tg_username": username}})
    await update.message.reply_text(f"✅ <b>{student.get('name')}</b> (<code>{student.get('roll_no')}</code>) ၏ Telegram Username ပြင်ဆင်ပြီးပါပြီ။", parse_mode=ParseMode.HTML)


async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    major = await get_user_major(user_id)
    if not await is_major_authorized(user_id, major):
        return
    if not context.args:
        return await update.message.reply_text("⚠️ <b>အသုံးပြုနည်း:</b> <code>/history &lt;RollNo&gt;</code>\n(ဥပမာ - <code>/history 01</code>)", parse_mode=ParseMode.HTML)

    roll_query = context.args[0]
    try:
        student = await find_student_by_roll_in_major(roll_query, major, allow_cross_major=is_super_admin(user_id))
    except (RollNumberError, CrossMajorAccessError) as e:
        return await update.message.reply_text(f"⚠️ {e}", parse_mode=ParseMode.HTML)

    if not student:
        return await update.message.reply_text(f"⚠️ <b>[{major.upper()}]</b> တွင် Roll <code>{roll_query}</code> ကျောင်းသားကို ရှာမတွေ့ပါ။", parse_mode=ParseMode.HTML)

    history = student.get("payment_history", [])
    if not history:
        return await update.message.reply_text(f"⚠️ <b>{student.get('name')}</b> တွင် ငွေသွင်းမှတ်တမ်း မရှိသေးပါ။", parse_mode=ParseMode.HTML)

    msg = f"📜 <b>[{major.upper()}] Payment History for {student.get('name')} ({student.get('roll_no')})</b>\n━━━━━━━━━━━━━━━━━━\n"
    for idx, h in enumerate(history, 1):
        snap = f" [{h['roll_no_snapshot']}]" if h.get("roll_no_snapshot") else ""
        msg += f"{idx}. <b>{str(h.get('fund_id', '')).upper()}</b> - {h.get('amount', 0):,} MMK{snap}\n"
        msg += f"   📅 Date: {h.get('date')}\n"
        msg += f"   👤 Received By: <code>{h.get('received_by', 'Unknown Admin')}</code>\n\n"

    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

# ========================= INLINE FUND MANAGEMENT (/funds) =========================
_FUND_MANAGE_KEY = "__plumber_fund_manage_v1"
_FUND_MANAGE_PREFIX = "fundmanage:"


def _fm_escape(value) -> str:
    return html.escape(str(value if value is not None else ""))


def _fm_buttons(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    rows.append([InlineKeyboardButton("✖️ Cancel", callback_data=f"{_FUND_MANAGE_PREFIX}cancel")])
    return InlineKeyboardMarkup(rows)


async def _count_unassigned_funds(db) -> int:
    """Count legacy fund documents that cannot safely be attributed to a major."""
    try:
        known_major_values = sorted({
            value
            for alias in MAJOR_ALIASES
            for value in (alias.lower(), alias.upper(), alias.capitalize())
        })
        return await db.funds.count_documents({
            "$and": [
                {"major": {"$nin": known_major_values}},
                {"type": {"$ne": "opening_balance"}},
            ]
        })
    except Exception:
        logger.exception("Could not count unassigned legacy fund documents")
        return 0


async def funds_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current-major fund list and inline management actions."""
    user_id = update.effective_user.id
    major = normalize_major(await get_user_major(user_id))
    if not major or not await is_major_authorized(user_id, major):
        return await update.message.reply_text("⛔ ကိုယ့် Major ရဲ့ EC/Admin များသာ `/funds` သုံးနိုင်ပါတယ်။")
    context.user_data.pop(_FLOW_KEY, None)
    await _funds_list_message(update.message, context, major, edit=False, page=0)


async def _funds_list_message(message, context, major: str, edit: bool, page: int = 0):
    db = get_db(major=major)
    if db is None:
        text = "❌ Database ချိတ်ဆက်မှု မရပါ။"
        return await (message.edit_text(text) if edit else message.reply_text(text))
    funds = await db.funds.find({"$and": [build_fund_major_filter(major), {"type": {"$ne": "opening_balance"}}]}).sort("created_at", -1).to_list(length=None)
    unassigned_count = await _count_unassigned_funds(db)
    warning = (
        f"\n\n⚠️ Major မသတ်မှတ်ထားသော သို့မဟုတ် မသိရှိနိုင်သော legacy Fund {unassigned_count} ခုကို "
        "မှားယွင်းစွာ မပြသစေရန် စာရင်းမှ ဖယ်ထားသည်။ Admin က ပိုင်ဆိုင်ရာ Major ကို စစ်ဆေးသတ်မှတ်ရန် လိုအပ်သည်။"
        if unassigned_count else ""
    )
    if not funds:
        text = f"📭 [{major.upper()}] မှာ Major သတ်မှတ်ပြီးသား Fund မရှိသေးပါ။ `/newfund` နဲ့ အသစ်ဖန်တီးနိုင်ပါတယ်။{warning}"
        return await (message.edit_text(text) if edit else message.reply_text(text))
    page_size = 7
    pages = max(1, (len(funds) + page_size - 1) // page_size)
    page = max(0, min(page, pages - 1))
    selected = funds[page * page_size:(page + 1) * page_size]
    rows = []
    for fund in selected:
        fid = str(fund.get("fund_id", ""))
        name = str(fund.get("fund_name") or fund.get("title") or fid)
        status = str(fund.get("status", "active")).upper()
        amount = int(fund.get("target_amount") or 0)
        rows.append([InlineKeyboardButton(f"{status} · {name[:27]} · {amount:,}", callback_data=f"{_FUND_MANAGE_PREFIX}view:{fid}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"{_FUND_MANAGE_PREFIX}page:{page-1}"))
    if page + 1 < pages:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"{_FUND_MANAGE_PREFIX}page:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("➕ New fund", callback_data=f"{_FUND_MANAGE_PREFIX}new")])
    rows.append([InlineKeyboardButton("📚 Semester history", callback_data=f"{_FUND_MANAGE_PREFIX}history")])
    keyboard = InlineKeyboardMarkup(rows)
    text = f"🗂 <b>[{major.upper()}] Funds</b>\nစာမျက်နှာ {page+1}/{pages}\nFund တစ်ခုရွေးပြီး ကြည့်ရန်/ပြင်ရန်။\n\nActive = အသုံးပြုနိုင်၊ Completed/Cancelled = မှတ်တမ်းထိန်းထားသော Fund။{warning}"
    if edit:
        return await message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    return await message.reply_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


async def _fund_manage_view(query, context, major: str, fund_id: str):
    db = get_db(major=major)
    if db is None:
        return await query.edit_message_text("❌ Database ချိတ်ဆက်မှု မရပါ။")
    fund = await db.funds.find_one({"$and": [build_fund_major_filter(major), {"fund_id": fund_id}]})
    if not fund:
        return await query.edit_message_text("⚠️ Fund မတွေ့ပါ။ `/funds` ကို ပြန်ဖွင့်ပါ။")
    context.user_data[_FUND_MANAGE_KEY] = {
        "actor_id": query.from_user.id, "major": major, "fund_id": fund_id,
        "fund_name": fund.get("fund_name") or fund.get("title") or fund_id,
    }
    status = str(fund.get("status", "active"))
    students = await db.student_info.find(build_major_filter(major)).to_list(length=None)
    target_amount = int(fund.get("target_amount") or 0)
    paid_count = 0
    partial_count = 0
    unpaid_students = 0
    for student in students:
        paid = _amount_of_student_fund(student, fund_id)
        if target_amount > 0 and paid >= target_amount:
            paid_count += 1
        elif paid > 0:
            partial_count += 1
        else:
            unpaid_students += 1
    assignment = "student_allocation" if fund.get("assignment_mode", "student_allocation") == "student_allocation" else "single_manager"
    mode_text = "Student allocation အတိုင်း EC ခွဲဝေ" if assignment == "student_allocation" else "EC တစ်ယောက်က Fund တစ်ခုလုံးတာဝန်ယူ"
    created_by = fund.get("created_by")
    creator_text = str(created_by) if created_by is not None else "Legacy record (မသိရှိ)"
    created_at_text = str(fund.get("created_at") or "မမှတ်တမ်းတင်ထား")
    body = (f"💰 <b>[{major.upper()}] Fund details</b>\n━━━━━━━━━━━━━━━━━━\n"
            f"🏷️ Name: <b>{_fm_escape(fund.get('fund_name') or fund.get('title') or fund_id)}</b>\n"
            f"🆔 ID: <code>{_fm_escape(fund_id)}</code>\n"
            f"💵 Per student: <b>{int(fund.get('target_amount') or 0):,} MMK</b>\n"
            f"👤 Responsible EC: <b>{_fm_escape(fund.get('managed_by') or 'allocation')}</b>\n"
            f"🧩 Assignment: <b>{mode_text}</b>\n"
            f"🎓 Semester: <code>{_fm_escape(fund.get('semester') or 'legacy')}</code>\n"
            f"📌 Status: <b>{_fm_escape(status)}</b>\n"
            f"🧾 Created by: <code>{_fm_escape(creator_text)}</code>\n"
            f"🕒 Created at: <code>{_fm_escape(created_at_text)}</code>\n"
            f"📊 Paid: <b>{paid_count}</b> · Partial: <b>{partial_count}</b> · Unpaid: <b>{unpaid_students}</b>")
    rows = [
        [InlineKeyboardButton("✏️ Edit name", callback_data=f"{_FUND_MANAGE_PREFIX}editname:{fund_id}"), InlineKeyboardButton("💵 Edit amount", callback_data=f"{_FUND_MANAGE_PREFIX}editamount:{fund_id}")],
        [InlineKeyboardButton("👤 Change responsible EC", callback_data=f"{_FUND_MANAGE_PREFIX}editmanager:{fund_id}")],
        [InlineKeyboardButton("🧩 Change assignment mode", callback_data=f"{_FUND_MANAGE_PREFIX}editmode:{fund_id}")],
        [InlineKeyboardButton("📋 View unpaid list", callback_data=f"{_FUND_MANAGE_PREFIX}unpaid:{fund_id}")],
    ]
    if status not in ("cancelled", "completed", "archived"):
        rows.append([InlineKeyboardButton("🚫 Cancel fund (keep history)", callback_data=f"{_FUND_MANAGE_PREFIX}cancelask:{fund_id}")])
    if status == "cancelled":
        rows.append([InlineKeyboardButton("♻️ Reactivate fund", callback_data=f"{_FUND_MANAGE_PREFIX}reactivate:{fund_id}")])
    rows.append([InlineKeyboardButton("⬅️ Back to funds", callback_data=f"{_FUND_MANAGE_PREFIX}list:0")])
    return await query.edit_message_text(body, reply_markup=InlineKeyboardMarkup(rows), parse_mode=ParseMode.HTML)


async def fund_manage_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    state = context.user_data.get(_FUND_MANAGE_KEY)
    if not isinstance(state, dict) or not state.get("input_step"):
        return
    if state.get("actor_id") != update.effective_user.id:
        return
    major = state.get("major")
    if not await is_major_authorized(update.effective_user.id, major):
        context.user_data.pop(_FUND_MANAGE_KEY, None)
        return await update.message.reply_text("⛔ Permission မရှိတော့ပါ။")
    db = get_db(major=major)
    fund_id = state.get("fund_id")
    fund = await db.funds.find_one({"$and": [build_fund_major_filter(major), {"fund_id": fund_id}]}) if db is not None else None
    if not fund:
        context.user_data.pop(_FUND_MANAGE_KEY, None)
        return await update.message.reply_text("⚠️ Fund မတွေ့တော့ပါ။")
    raw = update.message.text.strip()
    step = state.get("input_step")
    update_fields = {}
    if step == "name":
        if not 1 <= len(raw) <= 80:
            return await update.message.reply_text("Fund name ကို ၁ မှ ၈၀ လုံးအတွင်း ထည့်ပါ။")
        update_fields["fund_name"] = raw
    elif step == "amount":
        if not raw.isdigit() or not (1 <= int(raw) <= 1_000_000_000):
            return await update.message.reply_text("Amount ကို ၁ ထက်ကြီးတဲ့ ကိန်းဂဏန်းနဲ့ ထည့်ပါ။")
        update_fields["target_amount"] = int(raw)
    else:
        return
    update_fields["updated_at"] = mm_timestamp()
    update_fields["updated_by"] = update.effective_user.id
    await db.funds.update_one({"_id": fund["_id"]}, {"$set": update_fields})
    await log_audit(db, major=major, actor_id=update.effective_user.id, action=f"fund_{step}_updated",
                    entity_type="fund", entity_id=fund_id, target_name=raw,
                    details={"old_value": fund.get("fund_name") if step == "name" else fund.get("target_amount"),
                             "new_value": raw if step == "name" else int(raw)}, semester=fund.get("semester"))
    state.pop("input_step", None)
    return await update.message.reply_text(f"✅ Fund ကို ပြင်ဆင်ပြီးပါပြီ။ `/funds` မှာ ပြန်ကြည့်နိုင်ပါတယ်။")


async def fund_manage_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data[len(_FUND_MANAGE_PREFIX):]
    user_id = query.from_user.id
    state = context.user_data.get(_FUND_MANAGE_KEY, {})
    major = state.get("major") or normalize_major(await get_user_major(user_id))
    if not major or not await is_major_authorized(user_id, major):
        context.user_data.pop(_FUND_MANAGE_KEY, None)
        return await query.edit_message_text("⛔ ဒီ Major အတွက် ခွင့်ပြုချက် မရှိပါ။")
    if action == "cancel":
        context.user_data.pop(_FUND_MANAGE_KEY, None)
        return await query.edit_message_text("✖️ Fund management ကို ပိတ်လိုက်ပါပြီ။")
    if action == "new":
        context.user_data.pop(_FUND_MANAGE_KEY, None)
        return await query.edit_message_text("အသစ်ဖန်တီးရန် `/newfund` ကိုနှိပ်ပါ။", parse_mode=ParseMode.MARKDOWN)
    if action == "history":
        context.user_data.pop(_FUND_MANAGE_KEY, None)
        return await query.edit_message_text("Semester အဟောင်းစာရင်းအတွက် `/semhistory` ကိုသုံးပါ။", parse_mode=ParseMode.MARKDOWN)
    if action.startswith("list:"):
        try: page = int(action.split(":", 1)[1])
        except ValueError: page = 0
        return await _funds_list_message(query.message, context, major, edit=True, page=page)
    if action.startswith("page:"):
        try: page = int(action.split(":", 1)[1])
        except ValueError: page = 0
        return await _funds_list_message(query.message, context, major, edit=True, page=page)
    if action.startswith("view:"):
        return await _fund_manage_view(query, context, major, action.split(":", 1)[1])

    bits_for_action = action.split(":")
    if bits_for_action[0] in {"manager", "mode"}:
        fund_id = bits_for_action[1] if len(bits_for_action) > 1 else ""
    else:
        fund_id = bits_for_action[1] if len(bits_for_action) > 1 else ""
    db = get_db(major=major)
    fund = await db.funds.find_one({"$and": [build_fund_major_filter(major), {"fund_id": fund_id}]}) if db is not None else None
    if not fund:
        return await query.edit_message_text("⚠️ Fund မတွေ့ပါ။ `/funds` ကို ပြန်ဖွင့်ပါ။")
    status = fund.get("status", "active")
    if action.startswith("unpaid:"):
        fund_id = action.split(":", 1)[1]
        target_amount = int(fund.get("target_amount") or 0)
        students = await db.student_info.find(build_major_filter(major)).to_list(length=None)
        unpaid = []
        for student in students:
            paid = _amount_of_student_fund(student, fund_id)
            if paid < target_amount:
                roll = get_short_roll(student)
                remaining = max(0, target_amount - paid)
                unpaid.append((roll, student.get("name") or "Unknown", remaining))
        unpaid.sort(key=lambda row: int(row[0]) if str(row[0]).isdigit() else 9999)
        lines = [f"📋 <b>[{major.upper()}] Unpaid / Partial</b>",
                 f"Fund: <b>{_fm_escape(fund.get('fund_name') or fund_id)}</b>",
                 f"Target/student: {target_amount:,} MMK", "━━━━━━━━━━━━━━━━━━"]
        if unpaid:
            lines.extend(f"• <code>{_fm_escape(roll)}</code> — {_fm_escape(name)} · ကျန် {remaining:,} MMK" for roll, name, remaining in unpaid[:90])
            if len(unpaid) > 90:
                lines.append(f"… နောက်ထပ် {len(unpaid)-90} ယောက်ရှိပါသည်။")
        else:
            lines.append("✅ ကျောင်းသားအားလုံး ပေးပြီးပါပြီ။")
        lines.append(f"\nစုစုပေါင်း မပြီးသေးသူ: <b>{len(unpaid)}</b> ဦး")
        text = "\n".join(lines)
        if len(text) > 3900:
            text = text[:3800] + "\n…\n<i>စာရင်းရှည်လွန်းသဖြင့် အတိုချုံ့ထားသည်။</i>"
        return await query.edit_message_text(text, parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to fund", callback_data=f"{_FUND_MANAGE_PREFIX}view:{fund_id}")]]))
    if action.startswith(("editname:", "editamount:", "editmanager:", "editmode:")) and status != "active":
        return await query.edit_message_text("⚠️ Completed/Cancelled fund တွေဟာ history record ဖြစ်နေတဲ့အတွက် တိုက်ရိုက်မပြင်နိုင်ပါ။ လက်ရှိ semester အတွက် Fund အသစ်ဖန်တီးပါ။")
    if action.startswith("editname:") or action.startswith("editamount:"):
        state.update({"actor_id": user_id, "major": major, "fund_id": fund_id,
                      "input_step": "name" if action.startswith("editname:") else "amount"})
        prompt = "Fund name အသစ် ထည့်ပါ။" if state["input_step"] == "name" else "ကျောင်းသားတစ်ဦးချင်း ပေးရမယ့် amount အသစ်ကို MMK နဲ့ ထည့်ပါ။"
        context.user_data[_FUND_MANAGE_KEY] = state
        return await query.edit_message_text(prompt, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✖️ Cancel", callback_data=f"{_FUND_MANAGE_PREFIX}cancel")]]))
    if action.startswith("editmanager:"):
        ecs = await _active_ecs_for_major(major)
        if not ecs:
            return await query.edit_message_text("Active EC မရှိပါ။ `/addec` နဲ့ EC ကိုအရင် register လုပ်ပါ။")
        rows = [[InlineKeyboardButton("@" + str(ec["nickname"])[:30], callback_data=f"{_FUND_MANAGE_PREFIX}manager:{fund_id}:{int(ec['user_id'])}")] for ec in ecs]
        rows.append([InlineKeyboardButton("⬅️ Back", callback_data=f"{_FUND_MANAGE_PREFIX}view:{fund_id}")])
        return await query.edit_message_text("Fund တာဝန်ခံ EC အသစ် ရွေးပါ။", reply_markup=InlineKeyboardMarkup(rows))
    if action.startswith("manager:"):
        bits = action.split(":")
        if len(bits) != 3 or not bits[2].isdigit():
            return await query.edit_message_text("Callback data မမှန်ပါ။")
        fund_id, ec_id = bits[1], int(bits[2])
        ec = await get_db().ec_roles.find_one({"user_id": ec_id, "major": major, "active": {"$ne": False}})
        if not ec:
            return await query.edit_message_text("ရွေးထားတဲ့ EC သည် ဒီ Major မှာ Active မဖြစ်တော့ပါ။")
        old = fund.get("manager_user_id")
        old_mode = fund.get("assignment_mode", "student_allocation")
        await db.funds.update_one({"_id": fund["_id"]}, {"$set": {
            "assignment_mode": "single_manager",
            "manager_user_id": ec_id, "managed_by": str(ec.get("nickname") or ec_id),
            "updated_by": user_id, "updated_at": mm_timestamp()
        }})
        await log_audit(db, major=major, actor_id=user_id, action="fund_manager_updated", entity_type="fund",
                        entity_id=fund_id, target_user_id=ec_id, target_name=ec.get("nickname"),
                        details={"previous_manager_user_id": old, "previous_assignment_mode": old_mode,
                                 "new_assignment_mode": "single_manager"}, semester=fund.get("semester"))
        return await _fund_manage_view(query, context, major, fund_id)
    if action.startswith("editmode:"):
        rows = [[InlineKeyboardButton("👥 Student allocation", callback_data=f"{_FUND_MANAGE_PREFIX}mode:{fund_id}:student_allocation")],
                [InlineKeyboardButton("👤 Single EC manager", callback_data=f"{_FUND_MANAGE_PREFIX}mode:{fund_id}:single_manager")],
                [InlineKeyboardButton("⬅️ Back", callback_data=f"{_FUND_MANAGE_PREFIX}view:{fund_id}")]]
        return await query.edit_message_text("Fund assignment mode ရွေးပါ။ Student တစ်ဦးချင်းခွဲဝေမှုကို ရှိပြီးသား allocation command က စီမံပါမယ်။", reply_markup=InlineKeyboardMarkup(rows))
    if action.startswith("mode:"):
        bits = action.split(":")
        if len(bits) != 3 or bits[2] not in ("student_allocation", "single_manager"):
            return await query.edit_message_text("ရွေးချယ်မှု မမှန်ပါ။")
        fund_id, mode = bits[1], bits[2]
        if mode == "single_manager":
            ecs = await _active_ecs_for_major(major)
            if not ecs:
                return await query.edit_message_text("Active EC မရှိပါ။ `/addec` နဲ့ EC ကိုအရင် register လုပ်ပါ။")
            rows = [[InlineKeyboardButton("@" + str(ec["nickname"])[:30], callback_data=f"{_FUND_MANAGE_PREFIX}mode_manager:{fund_id}:{int(ec['user_id'])}")] for ec in ecs]
            rows.append([InlineKeyboardButton("⬅️ Back", callback_data=f"{_FUND_MANAGE_PREFIX}view:{fund_id}")])
            return await query.edit_message_text("Fund တစ်ခုလုံးကို တာဝန်ယူမယ့် EC ကို ရွေးပါ။", reply_markup=InlineKeyboardMarkup(rows))
        await db.funds.update_one({"_id": fund["_id"]}, {"$set": {"assignment_mode": mode, "managed_by": "ec", "updated_by": user_id, "updated_at": mm_timestamp()}, "$unset": {"manager_user_id": ""}})
        await log_audit(db, major=major, actor_id=user_id, action="fund_assignment_mode_updated", entity_type="fund",
                        entity_id=fund_id, details={"new_mode": mode}, semester=fund.get("semester"))
        return await _fund_manage_view(query, context, major, fund_id)
    if action.startswith("mode_manager:"):
        bits = action.split(":")
        if len(bits) != 3 or not bits[2].isdigit():
            return await query.edit_message_text("EC ရွေးချယ်မှု မမှန်ပါ။")
        fund_id, ec_id = bits[1], int(bits[2])
        ec = await get_db().ec_roles.find_one({"user_id": ec_id, "major": major, "active": {"$ne": False}})
        if not ec:
            # Legacy CEIT members may exist only in the compatibility mapping.
            ec = next((row for row in await _active_ecs_for_major(major) if int(row.get("user_id", 0)) == ec_id), None)
        if not ec:
            return await query.edit_message_text("ရွေးထားတဲ့ EC သည် ဒီ Major မှာ Active မဖြစ်တော့ပါ။")
        await db.funds.update_one({"_id": fund["_id"]}, {"$set": {
            "assignment_mode": "single_manager", "manager_user_id": ec_id,
            "managed_by": str(ec.get("nickname") or ec_id), "updated_by": user_id,
            "updated_at": mm_timestamp()
        }})
        await log_audit(db, major=major, actor_id=user_id, action="fund_assignment_mode_updated", entity_type="fund",
                        entity_id=fund_id, target_user_id=ec_id, target_name=ec.get("nickname"),
                        details={"new_mode": "single_manager"}, semester=fund.get("semester"))
        return await _fund_manage_view(query, context, major, fund_id)
    if action.startswith("cancelask:"):
        if status in ("cancelled", "completed", "archived"):
            return await query.edit_message_text("ဒီ Fund ကို ထပ် Cancel လုပ်ဖို့ မလိုပါ။")
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Confirm cancel (keep history)", callback_data=f"{_FUND_MANAGE_PREFIX}canceldo:{fund_id}")],
            [InlineKeyboardButton("⬅️ Back", callback_data=f"{_FUND_MANAGE_PREFIX}view:{fund_id}")],
        ])
        return await query.edit_message_text(
            f"⚠️ <b>{_fm_escape(fund.get('fund_name') or fund_id)}</b> ကို Cancel လုပ်မလား?\n\nရှိပြီးသား payment နဲ့ history တွေကို မဖျက်ဘဲ status ပဲပြောင်းပါမယ်။",
            reply_markup=keyboard, parse_mode=ParseMode.HTML)
    if action.startswith("canceldo:"):
        fund_id = action.split(":", 1)[1]
        await db.funds.update_one({"_id": fund["_id"]}, {"$set": {"status": "cancelled", "cancelled_at": mm_timestamp(), "cancelled_by": user_id}})
        await log_audit(db, major=major, actor_id=user_id, action="fund_cancelled", entity_type="fund", entity_id=fund_id,
                        target_name=fund.get("fund_name"), details={"previous_status": status}, semester=fund.get("semester"))
        return await query.edit_message_text("✅ Fund ကို Cancel လုပ်ပြီးပါပြီ။ Payment history ကို ထိန်းသိမ်းထားပါတယ်။")
    if action.startswith("reactivate:"):
        fund_id = action.split(":", 1)[1]
        current_semester, current_year = await get_major_semester_context(major)
        fund_semester = fund.get("semester")
        fund_year = fund.get("academic_year")
        if current_semester is not None and fund_semester is not None and str(current_semester) != str(fund_semester):
            return await query.edit_message_text("⚠️ ဒီ Fund က အရင် Semester ကဖြစ်လို့ ပြန်ဖွင့်မရပါ။ လက်ရှိ Semester အတွက် Fund အသစ်ဖန်တီးပါ။")
        if current_year and fund_year and str(current_year) != str(fund_year):
            return await query.edit_message_text("⚠️ ဒီ Fund က အရင် Academic Year ကဖြစ်လို့ ပြန်ဖွင့်မရပါ။")
        await db.funds.update_one({"_id": fund["_id"]}, {"$set": {"status": "active", "reactivated_at": mm_timestamp(), "reactivated_by": user_id}})
        await log_audit(db, major=major, actor_id=user_id, action="fund_reactivated", entity_type="fund", entity_id=fund_id,
                        target_name=fund.get("fund_name"), details={"previous_status": status}, semester=fund.get("semester"))
        return await _fund_manage_view(query, context, major, fund_id)
    return await query.edit_message_text("ဒီ action ကို မသိပါ။ `/funds` ကို ပြန်ဖွင့်ပါ။")
