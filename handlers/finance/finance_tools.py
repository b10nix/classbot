# handlers/finance/finance_tools.py

import csv
import io
import re
from datetime import datetime
from telegram import Update, InputFile, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from database.db import get_db
from utils.helpers import get_mm_time
from utils.auth import is_super_admin
from utils.constants import MAJORS
from services.audit_service import log_audit
from services.major_service import (
    get_user_major,
    is_major_authorized,
    normalize_major,
    resolve_ec_in_major
)
from services.roll_number_service import RollNumberError, parse_roll_number
from services.finance_service import (
    build_major_filter,
    build_fund_major_filter,
    compute_ec_dashboard,
    format_ec_dashboard_message,
    extract_paid_amount,
    get_short_roll
)


async def allocate_fund(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    major = await get_user_major(user_id)
    if not await is_major_authorized(user_id, major):
        return await update.message.reply_text("⛔ ဤ Command ကို သက်ဆိုင်ရာ Major Admin/EC များသာ သုံးခွင့်ရှိသည်။")

    if len(context.args) < 2:
        return await update.message.reply_text("⚠️ အသုံးပြုနည်း: /allocate [username_or_id] [amount]")

    target_name = context.args[0].replace("@", "").lower()
    try:
        amount = int(context.args[1])
    except ValueError:
        return await update.message.reply_text("⚠️ ငွေပမာဏသည် ဂဏန်းဖြစ်ရပါမည်။")

    receiver_id, _ = await resolve_ec_in_major(target_name, major)
    if not receiver_id:
        return await update.message.reply_text(f"⚠️ <b>[{major.upper()}]</b> တွင် ထို EC ကို ရှာမတွေ့ပါ။", parse_mode=ParseMode.HTML)

    sender_username = update.effective_user.username.lower() if update.effective_user.username else ""
    db = get_db(major=major)
    m_filter = build_major_filter(major)

    await db.ec_ledgers.update_one(
        {"$and": [m_filter, {"$or": [{"user_id": user_id}, {"ec_id": user_id}, {"username": sender_username}]}]},
        {"$set": {"major": major}, "$inc": {"transferred_out": amount}},
        upsert=True
    )

    await db.ec_ledgers.update_one(
        {"$and": [m_filter, {"$or": [{"user_id": receiver_id}, {"ec_id": receiver_id}, {"username": target_name}]}]},
        {"$set": {"major": major}, "$inc": {"transferred_in": amount}},
        upsert=True
    )

    await db.transfers.insert_one({
        "major": major,
        "from_id": user_id,
        "to_id": receiver_id,
        "amount": amount,
        "date": get_mm_time()
    })

    await update.message.reply_text(
        f"✅ <b>[{major.upper()}]</b> EC <b>{context.args[0]}</b> ထံသို့ {amount:,} MMK လွှဲပြောင်းပေးပြီးပါပြီ။",
        parse_mode=ParseMode.HTML
    )


async def spend_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or ""
    major = await get_user_major(user_id)

    if not await is_major_authorized(user_id, major):
        return await update.message.reply_text("⛔ သင်သည် EC စာရင်းတွင် မရှိပါ။")

    if not context.args or len(context.args) < 2:
        return await update.message.reply_text(
            "⚠️ <b>အသုံးပြုနည်း:</b> <code>/spend &lt;ပမာဏ&gt; &lt;အကြောင်းအရာ&gt;</code>\n"
            "<i>ဥပမာ: /spend 38000 ဝါဆိုကြေး</i>",
            parse_mode=ParseMode.HTML
        )

    try:
        spend_amount = int(context.args[0])
        reason = " ".join(context.args[1:])
    except ValueError:
        return await update.message.reply_text("⚠️ ကျေးဇူးပြု၍ ပမာဏကို ဂဏန်းသီးသန့် ရိုက်ထည့်ပါ။")

    dash_data = await compute_ec_dashboard(user_id, [username], major=major)
    current_balance = dash_data["net_in_hand"]

    if spend_amount > current_balance:
        return await update.message.reply_text(
            f"❌ <b>[{major.upper()}] ငွေမလုံလောက်ပါ။</b>\n\n"
            f"💰 လက်ရှိသုံးစွဲနိုင်သော လက်ကျန်ငွေ: <b>{current_balance:,} MMK</b>\n"
            f"💸 သုံးစွဲလိုသော ပမာဏ: <b>{spend_amount:,} MMK</b>",
            parse_mode=ParseMode.HTML
        )

    new_total_spent = dash_data["total_spent"] + spend_amount
    db = get_db(major=major)

    await db.ec_ledgers.update_one(
        {"$and": [build_major_filter(major), {"$or": [{"ec_id": user_id}, {"user_id": user_id}, {"username": username.lower()}]}]},
        {
            "$set": {
                "major": major,
                "ec_id": user_id,
                "username": username.lower(),
                "total_spent": new_total_spent
            },
            "$push": {
                "expenses": {
                    "amount": spend_amount,
                    "reason": reason,
                    "date": update.message.date.strftime("%Y-%m-%d %H:%M")
                }
            }
        },
        upsert=True
    )

    try:
        await log_audit(db, major=major, actor_id=user_id, action="ec_spend", entity_type="ec_ledger",
                        entity_id=str(user_id), target_name=username,
                        details={"amount": spend_amount, "reason": reason}, semester=None)
    except Exception:
        pass

    remaining = current_balance - spend_amount
    await update.message.reply_text(
        f"✅ <b>[{major.upper()}] သုံးစွဲမှု မှတ်တမ်းတင်ပြီးပါပြီ!</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👤 EC: @{username}\n"
        f"💸 သုံးစွဲငွေ: <b>{spend_amount:,} MMK</b>\n"
        f"📝 အကြောင်းအရာ: <b>{reason}</b>\n"
        f"💰 လက်ထဲတွင် ကျန်ငွေ: <b>{remaining:,} MMK</b>",
        parse_mode=ParseMode.HTML
    )


async def _ecstat_active_members(major: str | None = None) -> list[dict]:
    shared = get_db()
    if shared is None:
        return []
    query = {"role": "ec", "active": {"$ne": False}}
    if major and major != "all":
        query["major"] = major
    rows = await shared.ec_roles.find(query).sort([("major", 1), ("nickname", 1)]).to_list(length=None)
    members = []
    seen = set()
    for row in rows:
        m = normalize_major(row.get("major"))
        uid = row.get("user_id")
        if not m or not uid:
            continue
        key = (m, int(uid))
        if key in seen:
            continue
        seen.add(key)
        members.append({"major": m, "user_id": int(uid), "nickname": str(row.get("nickname") or uid)})
    return members


async def _ecstat_show_major_picker(target, *, edit: bool, allow_all: bool = True):
    rows = []
    if allow_all:
        rows.append([InlineKeyboardButton("🌐 All Majors", callback_data="ecstat:major:all")])
    for major in MAJORS:
        rows.append([InlineKeyboardButton(major.upper(), callback_data=f"ecstat:major:{major}")])
    keyboard = InlineKeyboardMarkup(rows)
    text = "📊 <b>EC Statistics</b>\nMajor ရွေးချယ်ပါ။"
    if edit:
        return await target.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    return await target.reply_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


async def _ecstat_show_member_list(target, context, major: str, *, edit: bool, offset: int = 0):
    members = await _ecstat_active_members(major)
    page_size = 8
    if major == "all":
        title = "All Majors"
    else:
        title = major.upper()
    if not members:
        text = f"📭 [{title}] မှာ active EC မရှိပါ။"
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="ecstat:backmajor")]])
        if edit:
            return await target.edit_message_text(text, reply_markup=keyboard)
        return await target.reply_text(text, reply_markup=keyboard)
    pages = max(1, (len(members) + page_size - 1) // page_size)
    offset = max(0, min(offset, pages - 1))
    chosen = members[offset * page_size:(offset + 1) * page_size]
    rows = []
    for member in chosen:
        label = f"[{member['major'].upper()}] @{member['nickname']}"
        rows.append([InlineKeyboardButton(label[:58], callback_data=f"ecstat:ec:{member['major']}:{member['user_id']}")])
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"ecstat:list:{major}:{offset-1}"))
    if offset + 1 < pages:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"ecstat:list:{major}:{offset+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("⬅️ Change Major", callback_data="ecstat:backmajor")])
    text = f"👥 <b>[{title}] EC List</b> · {offset+1}/{pages}\nကြည့်လိုသော EC ကို ရွေးပါ။"
    keyboard = InlineKeyboardMarkup(rows)
    if edit:
        return await target.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    return await target.reply_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


async def ecstat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inline EC picker. ECs are scoped to their own major; super admins can pick any major."""
    user_id = update.effective_user.id
    if is_super_admin(user_id):
        if context.args:
            requested = normalize_major(context.args[0])
            if context.args[0].lower() == "all":
                return await _ecstat_show_member_list(update.message, context, "all", edit=False)
            if not requested:
                return await update.message.reply_text("Major မှန်ကန်စွာ ထည့်ပါ။ ဥပမာ `/ecstat ceit` သို့မဟုတ် `/ecstat all`")
            return await _ecstat_show_member_list(update.message, context, requested, edit=False)
        return await _ecstat_show_major_picker(update.message, edit=False, allow_all=True)

    major = normalize_major(await get_user_major(user_id))
    if not major or not await is_major_authorized(user_id, major):
        return await update.message.reply_text("⛔ EC Statistics ကို ကိုယ့် Major ရဲ့ EC များသာ ကြည့်ခွင့်ရှိပါတယ်။")
    return await _ecstat_show_member_list(update.message, context, major, edit=False)


async def ecstat_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()
    parts = query.data.split(":")
    if len(parts) < 2:
        return await query.edit_message_text("ဒီ action သက်တမ်းကုန်သွားပါပြီ။")
    action = parts[1]
    super_admin = is_super_admin(user_id)
    if action == "backmajor":
        if super_admin:
            return await _ecstat_show_major_picker(query, edit=True, allow_all=True)
        major = normalize_major(await get_user_major(user_id))
        if major and await is_major_authorized(user_id, major):
            return await _ecstat_show_member_list(query, context, major, edit=True)
        return await query.edit_message_text("⛔ Permission မရှိပါ။")
    if action == "major" and len(parts) == 3:
        if not super_admin:
            return await query.edit_message_text("⛔ Major ရွေးချယ်ခွင့်ကို Super Admin သာ အသုံးပြုနိုင်ပါတယ်။")
        major = parts[2]
        if major != "all" and major not in MAJORS:
            return await query.edit_message_text("Major မမှန်ပါ။")
        return await _ecstat_show_member_list(query, context, major, edit=True)
    if action == "list" and len(parts) == 4:
        major, offset_text = parts[2], parts[3]
        if major != "all" and major not in MAJORS:
            return await query.edit_message_text("Major မမှန်ပါ။")
        if not super_admin:
            own_major = normalize_major(await get_user_major(user_id))
            if major != own_major or not await is_major_authorized(user_id, major):
                return await query.edit_message_text("⛔ ကိုယ့် Major အတွက်သာ ကြည့်ခွင့်ရှိပါတယ်။")
        try:
            offset = int(offset_text)
        except ValueError:
            offset = 0
        return await _ecstat_show_member_list(query, context, major, edit=True, offset=offset)
    if action == "ec" and len(parts) == 4:
        major, target_text = parts[2], parts[3]
        if major not in MAJORS or not target_text.isdigit():
            return await query.edit_message_text("ရွေးချယ်မှု မမှန်ပါ။")
        if not super_admin and (normalize_major(await get_user_major(user_id)) != major or not await is_major_authorized(user_id, major)):
            return await query.edit_message_text("⛔ ကိုယ့် Major အတွက်သာ ကြည့်ခွင့်ရှိပါတယ်။")
        target_id = int(target_text)
        allowed_members = await _ecstat_active_members(major)
        if not any(int(member["user_id"]) == target_id for member in allowed_members):
            return await query.edit_message_text("ဒီ EC ကို လက်ရှိ Major အတွက် register လုပ်ထားခြင်း မတွေ့ပါ။")
        db = get_db(major=major)
        nick = await resolve_ec_in_major(target_id, major)
        target_name = nick[1] if isinstance(nick, tuple) else str(target_id)
        username = target_name
        try:
            chat = await context.bot.get_chat(target_id)
            username = chat.username or chat.full_name or target_name
        except Exception:
            pass
        data = await compute_ec_dashboard(target_id, [username, target_name, str(target_id)], major=major)
        if not data.get("has_records"):
            base = f"📊 <b>[{major.upper()}] @{target_name}</b>\nဒီ EC အတွက် finance records မရှိသေးပါ။"
        else:
            base = format_ec_dashboard_message(username, data, viewed_by_admin=super_admin)
        # Explicit fund ownership list (manager id/name or existing allocation ownership).
        fund_filter = build_fund_major_filter(major)
        funds = await db.funds.find({"$and": [fund_filter, {"type": {"$ne": "opening_balance"}}]}).sort("created_at", -1).to_list(length=None) if db is not None else []
        own_funds = [f for f in funds if f.get("manager_user_id") == target_id or str(f.get("managed_by", "")).lower() == target_name.lower()]
        if own_funds:
            base += "\n\n<b>Fund တာဝန်ယူမှု</b>\n"
            for fund in own_funds[:12]:
                base += f"• {_html_report(fund.get('fund_name') or fund.get('fund_id'))} — {int(fund.get('target_amount') or 0):,} MMK ({_html_report(fund.get('status', 'active'))})\n"
        # Future audit logging is append-only. Older actions that were never logged cannot be reconstructed.
        audit_rows = []
        if db is not None:
            audit_rows = await db.audit_logs.find({"$and": [build_major_filter(major), {"$or": [{"actor_id": target_id}, {"target_user_id": target_id}]}]}).sort("timestamp", -1).to_list(length=8)
        base += "\n\n<b>Recent activity</b>\n"
        if audit_rows:
            for row in audit_rows:
                stamp = _html_report(row.get("timestamp", ""))
                action_text = _html_report(row.get("action", "action"))
                entity = _html_report(row.get("entity_id") or row.get("target_name") or "")
                base += f"• {stamp} — <code>{action_text}</code> {entity}\n"
        else:
            base += "<i>Audit log ကို စတင်အသုံးပြုချိန်ကစပြီး activity အသစ်များကိုသာ ပြနိုင်ပါမယ်။</i>\n"
        base += "\n< i >Old semester data ကို /semhistory မှ ကြည့်နိုင်ပါတယ်။</i>".replace("< i >", "<i>")
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to EC list", callback_data=f"ecstat:list:{major}:0")]])
        # Telegram's HTML message limit is 4096 chars.
        if len(base) > 3900:
            base = base[:3850] + "\n…\n<i>စာသားရှည်လွန်းသဖြင့် အတိုချုံ့ပြထားသည်။</i>"
        return await query.edit_message_text(base, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    return await query.edit_message_text("ဒီ button ကို နားမလည်ပါ။ `/ecstat` ကို ပြန်စပါ။", parse_mode=ParseMode.HTML)


def _html_report(value) -> str:
    import html
    return html.escape(str(value if value is not None else ""))


async def assign_ec_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    major = await get_user_major(user_id)
    if not await is_major_authorized(user_id, major):
        return

    if len(context.args) < 2:
        return await update.message.reply_text(
            "⚠️ <b>အသုံးပြုနည်း:</b> <code>/assign_ec [nickname] [roll1] [roll2] ...</code>\n"
            "(ဥပမာ - <code>/assign_ec jojo 01 02 03</code>)",
            parse_mode=ParseMode.HTML
        )

    nickname = context.args[0].lower().lstrip("@")
    ec_id, _ = await resolve_ec_in_major(nickname, major)
    if not ec_id:
        return await update.message.reply_text(
            f"❌ <b>[{major.upper()}]</b> မေဂျာတွင် EC Nickname '<b>{nickname}</b>' မတွေ့ပါ။\n"
            f"<i>EC အသစ်ထည့်လိုပါက /addec {major} {nickname} &lt;telegram_id&gt; ဖြင့် အရင်ထည့်ပါ။</i>",
            parse_mode=ParseMode.HTML
        )

    raw_rolls = context.args[1:]
    or_conditions = []
    for r in raw_rolls:
        try:
            parsed = parse_roll_number(r)
            if parsed["major"] and parsed["major"] != major:
                return await update.message.reply_text(
                    f"⛔ <b>Major Mismatch:</b> <code>{r}</code> သည် <b>[{major.upper()}]</b> မှ မဟုတ်ပါ။",
                    parse_mode=ParseMode.HTML
                )
            r_int = parsed["roll_number"]
            or_conditions.append({"roll_number": r_int})
            or_conditions.append({"roll_no": {"$regex": rf"(?:^|-)0*{r_int}$", "$options": "i"}})
        except RollNumberError as e:
            return await update.message.reply_text(f"⚠️ {e}", parse_mode=ParseMode.HTML)

    if not or_conditions:
        return await update.message.reply_text("⚠️ မှန်ကန်သော Roll Number များ ထည့်ပေးပါ။")

    db = get_db(major=major)
    query = {"$and": [build_major_filter(major), {"$or": or_conditions}]}
    result = await db.student_info.update_many(
        query,
        {"$set": {"assigned_ec_id": ec_id, "assigned_ec": nickname}}
    )

    await update.message.reply_text(
        f"✅ <b>[{major.upper()}] EC @{nickname}</b> ထံသို့ ကျောင်းသား ({result.modified_count}) ယောက်ကို အောင်မြင်စွာ Assign လုပ်ပြီးပါပြီ။",
        parse_mode=ParseMode.HTML
    )


async def ec_me(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or ""
    major = await get_user_major(user_id)
    if not major:
        return await update.message.reply_text("⚠️ သင်၏ Major ကို အတည်ပြု၍ မရပါ။ Admin ထံ ဆက်သွယ်ပါ။")

    data = await compute_ec_dashboard(user_id, [username], major=major)
    if not data["has_records"]:
        return await update.message.reply_text(f"⚠️ <b>[{major.upper()}]</b> တွင် သင်သည် EC စာရင်းမရှိပါ သို့မဟုတ် ရငွေ/သုံးငွေ မှတ်တမ်း မရှိသေးပါ။", parse_mode=ParseMode.HTML)

    display_title = username if username else str(user_id)
    msg = format_ec_dashboard_message(display_title, data, viewed_by_admin=False)
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)


async def archive_fund_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    major = await get_user_major(user_id)
    if not await is_major_authorized(user_id, major):
        return
    if not context.args:
        return await update.message.reply_text("⚠️ <b>အသုံးပြုနည်း:</b> <code>/archive_fund &lt;FundID&gt;</code>", parse_mode=ParseMode.HTML)

    fund_id = context.args[0].lower()
    db = get_db(major=major)
    res = await db.funds.update_one(
        {"$and": [build_fund_major_filter(major), {"fund_id": fund_id}]},
        {"$set": {"status": "archived"}}
    )

    if res.modified_count:
        await update.message.reply_text(
            f"✅ <b>[{major.upper()}]</b> ရန်ပုံငွေ <code>{fund_id}</code> အား ဆရာမထံ အပ်နှံပြီးကြောင်း မှတ်တမ်းတင်လိုက်ပါပြီ။",
            parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text(f"⚠️ <b>[{major.upper()}]</b> တွင် ထို Fund ID မရှိပါ (သို့) Archive လုပ်ပြီးသား ဖြစ်နေပါသည်။", parse_mode=ParseMode.HTML)


async def unarchive_fund_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    major = await get_user_major(user_id)
    if not await is_major_authorized(user_id, major):
        return
    if not context.args:
        return await update.message.reply_text("⚠️ <b>အသုံးပြုနည်း:</b> <code>/unarchive_fund &lt;FundID&gt;</code>", parse_mode=ParseMode.HTML)

    fund_id = context.args[0].lower()
    db = get_db(major=major)

    res = await db.funds.update_one(
        {"$and": [build_fund_major_filter(major), {"fund_id": fund_id}]},
        {"$set": {"status": "active"}}
    )
    if major == "ceit":
        await db.sys_config.update_one({"type": "archived_funds"}, {"$pull": {"funds": fund_id}})

    if res.modified_count > 0 or res.matched_count > 0:
        await update.message.reply_text(
            f"✅ <b>[{major.upper()}]</b> ရန်ပုံငွေ <code>{fund_id}</code> အား <b>Active</b> အဖြစ် ပြန်လည်ပြောင်းလဲလိုက်ပါပြီ။",
            parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text(f"⚠️ <b>[{major.upper()}]</b> တွင် <code>{fund_id}</code> အမည်ဖြင့် ရန်ပုံငွေ ရှာမတွေ့ပါ။", parse_mode=ParseMode.HTML)


async def export_finance_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    major = await get_user_major(user_id)
    if not await is_major_authorized(user_id, major):
        return

    await update.message.reply_text(f"⏳ <b>[{major.upper()}]</b> စာရင်းရှင်းတမ်း ဖိုင်ထုတ်လုပ်နေပါသည်...", parse_mode=ParseMode.HTML)
    db = get_db(major=major)
    m_filter = build_major_filter(major)

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([f"--- {major.upper()} Student Fund Collection ---"])
    writer.writerow(["Roll No", "Name", "Telegram", "Fund Name", "Paid Amount"])

    students = await db.student_info.find(m_filter).to_list(length=None)
    total_collected = 0

    for st in students:
        paid_funds = st.get("paid_funds", {})
        for fund_name, amount in paid_funds.items():
            paid_val = extract_paid_amount(amount)
            writer.writerow([
                st.get("roll_no", ""),
                st.get("name", ""),
                f"@{st.get('tg_username', '')}",
                fund_name,
                paid_val
            ])
            total_collected += paid_val

    writer.writerow(["", "", "", "Total Collected:", total_collected])
    writer.writerow([])

    writer.writerow([f"--- {major.upper()} EC Expenses Report ---"])
    writer.writerow(["EC Username", "Date", "Reason", "Amount Spent"])

    ec_ledgers = await db.ec_ledgers.find(m_filter).to_list(length=None)
    total_ec_spent = 0

    for ec in ec_ledgers:
        for exp in ec.get("expenses", ec.get("expense_history", [])):
            writer.writerow([
                f"@{ec.get('username', '')}",
                exp.get("date", ""),
                exp.get("reason", ""),
                exp.get("amount", 0)
            ])
            total_ec_spent += exp.get("amount", 0)

    writer.writerow(["", "", "Total EC Spent:", total_ec_spent])

    output.seek(0)
    file_name = f"{major.upper()}_Finance_Report_{datetime.now().strftime('%Y_%m_%d')}.csv"
    csv_file = InputFile(io.BytesIO(output.getvalue().encode('utf-8-sig')), filename=file_name)

    await context.bot.send_document(
        chat_id=update.effective_chat.id,
        document=csv_file,
        caption=f"📊 <b>[{major.upper()}]</b> လစဉ်/Term အလိုက် ငွေစာရင်း ရှင်းတမ်း CSV ဖိုင် ရပါပြီ။",
        parse_mode=ParseMode.HTML
    )


async def fundstat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    major = await get_user_major(user_id)
    if not await is_major_authorized(user_id, major):
        return

    if not context.args:
        return await update.message.reply_text(
            "⚠️ <b>အသုံးပြုနည်း:</b> <code>/fundstat &lt;fund_id&gt;</code>\n(ဥပမာ - /fundstat math)",
            parse_mode=ParseMode.HTML
        )

    fund_id = context.args[0].lower()
    db = get_db(major=major)
    m_filter = build_major_filter(major)

    fund_info = await db.funds.find_one({
        "$and": [build_fund_major_filter(major), {"fund_id": re.compile(f"^{re.escape(fund_id)}$", re.IGNORECASE)}]
    })
    if not fund_info:
        return await update.message.reply_text(f"⚠️ <b>[{major.upper()}]</b> တွင် ထို Fund ကို ရှာမတွေ့ပါ။", parse_mode=ParseMode.HTML)

    fund_name = fund_info.get("fund_name", fund_id.capitalize())
    target_amount = fund_info.get("target_amount", 0)
    manager = fund_info.get("managed_by", "ec")

    all_students = await db.student_info.find(m_filter).to_list(length=None)
    ec_collections = {}
    total_collected = 0
    unpaid_count = 0

    for st in all_students:
        short_roll = get_short_roll(st)
        paid_data = st.get("paid_funds", {}).get(fund_id)
        if not paid_data:
            unpaid_count += 1
            continue

        amount_paid = extract_paid_amount(paid_data)
        if isinstance(paid_data, dict):
            collected_by_data = paid_data.get("collected_by", "ec")
            if isinstance(collected_by_data, dict):
                for ec_name, ec_amt in collected_by_data.items():
                    if ec_amt > 0:
                        display_ec = ec_name.capitalize()
                        ec_collections.setdefault(display_ec, {"amount": 0, "rolls": set()})
                        ec_collections[display_ec]["amount"] += ec_amt
                        ec_collections[display_ec]["rolls"].add(short_roll)
            elif isinstance(collected_by_data, str) and amount_paid > 0:
                display_ec = collected_by_data.capitalize()
                ec_collections.setdefault(display_ec, {"amount": 0, "rolls": set()})
                ec_collections[display_ec]["amount"] += amount_paid
                ec_collections[display_ec]["rolls"].add(short_roll)
        elif isinstance(paid_data, (int, float)) and amount_paid > 0:
            display_ec = st.get("assigned_ec", "ec").capitalize()
            ec_collections.setdefault(display_ec, {"amount": 0, "rolls": set()})
            ec_collections[display_ec]["amount"] += amount_paid
            ec_collections[display_ec]["rolls"].add(short_roll)

        if amount_paid > 0:
            total_collected += amount_paid
        else:
            unpaid_count += 1

    msg = (
        f"📊 <b>[{major.upper()}] FUND REPORT: {fund_name}</b>\n"
        f"🎯 Target Amount: {target_amount:,} MMK / student\n"
        f"👤 Manager: <b>@{manager}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"📥 <b>EC များ၏ ကောက်ခံပေးသွင်းမှု:</b>\n"
    )
    if ec_collections:
        for ec_name, data in ec_collections.items():
            msg += f"• {ec_name}: {data['amount']:,} MMK ({len(data['rolls'])} ဦး)\n"
    else:
        msg += "<i>လက်ရှိ ကောက်ခံရရှိထားသော ငွေမရှိသေးပါ</i>\n"

    msg += f"💰 <b>စုစုပေါင်း ကောက်ခံရငွေ: {total_collected:,} MMK</b>\n\n━━━━━━━━━━━━━━━━━━\n\n"
    msg += "✅ <b>ပေးချေပြီးသော ကျောင်းသားများ (Roll No):</b>\n"
    if ec_collections:
        for ec_name, data in ec_collections.items():
            rolls_str = ", ".join(sorted(list(data["rolls"])))
            msg += f"{ec_name} : {rolls_str}\n"
    else:
        msg += "<i>မရှိသေးပါ</i>\n"

    msg += f"\n❌ မပေးရသေးသော ကျောင်းသားများ: <b>{unpaid_count}</b> ဦး"
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)