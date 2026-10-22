# handlers/admin/major_admin.py

import html
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from database.db import get_db
from utils.auth import is_super_admin
from utils.constants import CLASS_GROUP_IDS
from services.major_service import (
    VALID_MAJORS,
    normalize_major,
    get_user_major
)
from services.roll_number_service import (
    RollNumberError,
    normalize_major_strict,
    normalize_semester_input,
    get_academic_setting,
    set_current_semester,
    advance_major_students_semester,
    reconcile_legacy_student_records
)


async def setmajor_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Switch active Major workspace for Super Admins."""
    user_id = update.effective_user.id
    if not is_super_admin(user_id):
        return await update.message.reply_text("⛔ ဤ Command ကို Super Admin များသာ သုံးခွင့်ရှိသည်။")

    current_major = await get_user_major(user_id)
    if not context.args:
        majors_str = ", ".join(m.upper() for m in VALID_MAJORS)
        return await update.message.reply_text(
            f"🏛 <b>လက်ရှိ Active Major:</b> <code>{current_major.upper()}</code>\n\n"
            f"ပြောင်းလဲလိုပါက: <code>/setmajor &lt;major&gt;</code>\n"
            f"Available Majors: <code>{majors_str}</code>",
            parse_mode=ParseMode.HTML
        )

    target_major = normalize_major(context.args[0])
    if not target_major:
        majors_str = ", ".join(m.upper() for m in VALID_MAJORS)
        return await update.message.reply_text(
            f"❌ Major အမည် မှားယွင်းနေပါသည်။\nရွေးချယ်နိုင်သော Major များ: <code>{majors_str}</code>",
            parse_mode=ParseMode.HTML
        )

    db = get_db()
    await db.user_settings.update_one(
        {"user_id": user_id},
        {"$set": {"active_major": target_major}},
        upsert=True
    )

    await update.message.reply_text(
        f"✅ သင်၏ Active Finance/Admin Workspace ကို <b>[{target_major.upper()}]</b> မေဂျာသို့ ပြောင်းလဲလိုက်ပါပြီ။",
        parse_mode=ParseMode.HTML
    )


async def setsemester_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Inspect or update active semester for a major with confirmation preview.
    Usage:
      /setsemester                     -> View all configured semesters
      /setsemester <major> <semester> [academic_year]
    Example:
      /setsemester ceit 3 2026-2027
    """
    user_id = update.effective_user.id
    if not is_super_admin(user_id):
        return await update.message.reply_text("⛔ ဤ Command ကို Super Admin များသာ သုံးခွင့်ရှိသည်။")

    db = get_db()
    if not context.args:
        docs = await db.academic_settings.find({}).sort("major", 1).to_list(length=None)
        if not docs:
            return await update.message.reply_text(
                "📭 <b>Academic Semester သတ်မှတ်ထားခြင်း မရှိသေးပါ။</b>\n"
                "သတ်မှတ်ရန်: <code>/setsemester &lt;major&gt; &lt;1-12&gt; [academic_year]</code>",
                parse_mode=ParseMode.HTML
            )
        msg = "🎓 <b>CONFIGURED MAJOR SEMESTERS</b>\n━━━━━━━━━━━━━━━━━━\n"
        for d in docs:
            m = d.get("major", "").upper()
            sem = d.get("current_semester")
            rom = d.get("semester_roman", "")
            yr = d.get("academic_year", "N/A")
            msg += f"• <b>{m}:</b> Semester {sem} (<code>Sem-{rom}-{m}-XX</code>) [{yr}]\n"
        return await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

    if len(context.args) < 2:
        return await update.message.reply_text(
            "⚠️ <b>အသုံးပြုနည်း:</b> <code>/setsemester &lt;major&gt; &lt;semester&gt; [academic_year]</code>\n"
            "ဥပမာ: <code>/setsemester ceit 3 2026-2027</code>",
            parse_mode=ParseMode.HTML
        )

    try:
        canon_major = normalize_major_strict(context.args[0])
        sem_int, sem_roman = normalize_semester_input(context.args[1])
    except RollNumberError as e:
        return await update.message.reply_text(f"❌ {html.escape(str(e))}", parse_mode=ParseMode.HTML)

    acad_year = context.args[2].strip() if len(context.args) >= 3 else "2026-2027"
    existing = await get_academic_setting(canon_major)
    prev_sem = existing.get("current_semester", "Not Set") if existing else "Not Set"

    kb = [
        [
            InlineKeyboardButton(
                "✅ Confirm (Setting Only)",
                callback_data=f"semcfg_apply_{canon_major}_{sem_int}_{acad_year}"
            )
        ],
        [
            InlineKeyboardButton(
                "🔄 Confirm & Advance Existing Roll Prefixes",
                callback_data=f"semcfg_advance_{canon_major}_{sem_int}_{acad_year}"
            )
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="semcfg_cancel")]
    ]

    await update.message.reply_text(
        f"⚙️ <b>CONFIRM SEMESTER CHANGE</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"🏛 <b>Major:</b> {canon_major.upper()}\n"
        f"⏪ <b>Previous Semester:</b> {prev_sem}\n"
        f"⏩ <b>New Semester:</b> {sem_int} (<code>Sem-{sem_roman}-{canon_major.upper()}-01</code>)\n"
        f"📅 <b>Academic Year:</b> {html.escape(acad_year)}\n\n"
        f"<i>• Setting Only: နောက်ထပ် စာရင်းသွင်းမည့်သူများအတွက်သာ သက်ရောက်မည်။\n"
        f"• Advance Roll Prefixes: လက်ရှိ ကျောင်းသားများ၏ Roll Prefix ကိုပါ Sem-{sem_roman} သို့ တိုးပေးမည် (ငွေစာရင်းဟောင်းများ လုံးဝ မပျက်ပါ)။</i>",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.HTML
    )


async def setsemester_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    if not is_super_admin(user_id):
        return await query.answer("⛔ Super Admin Only", show_alert=True)

    await query.answer()
    data = query.data
    if data == "semcfg_cancel":
        return await query.edit_message_text("🚫 Semester ပြောင်းလဲခြင်းကို ပယ်ဖျက်လိုက်ပါပြီ။")

    parts = data.split("_", 4)
    if len(parts) < 5:
        return
    mode, major, sem_str, acad_year = parts[1], parts[2], parts[3], parts[4]
    sem_int = int(sem_str)

    if mode == "apply":
        audit = await set_current_semester(major, sem_int, user_id, academic_year=acad_year)
        await query.edit_message_text(
            f"✅ <b>[{major.upper()}] Semester Configuration Updated</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"• Previous: <code>{audit['previous_semester']}</code> ➡️ New: <code>{audit['new_semester']}</code>\n"
            f"• Academic Year: <code>{html.escape(acad_year)}</code>\n"
            f"• Audit Timestamp: <code>{audit['timestamp']}</code>",
            parse_mode=ParseMode.HTML
        )
    elif mode == "advance":
        res = await advance_major_students_semester(major, sem_int, user_id)
        await query.edit_message_text(
            f"✅ <b>[{major.upper()}] Semester Advanced to Sem-{res['semester_roman']}</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"• Active Semester: <b>{res['new_semester']}</b>\n"
            f"• Updated Student Roll Prefixes: <b>{res['students_updated']}</b> ဦး\n"
            f"<i>(ကျောင်းသားများ၏ မူလ Roll History နှင့် ငွေစာရင်း/Attendance မှတ်တမ်းများအားလုံး မူလအတိုင်း ထိန်းသိမ်းထားပါသည်)</i>",
            parse_mode=ParseMode.HTML
        )


async def setgroupmajor_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Binds a Telegram Group to an authoritative Major for automatic user major detection.
    Usage: /setgroupmajor <major> [group_id]
    If run inside a group without group_id, binds the current group.
    """
    user_id = update.effective_user.id
    if not is_super_admin(user_id):
        return

    if not context.args:
        return await update.message.reply_text(
            "⚠️ <b>အသုံးပြုနည်း:</b>\n"
            "Group အတွင်းရိုက်ရန်: <code>/setgroupmajor &lt;major&gt;</code>\n"
            "DM မှရိုက်ရန်: <code>/setgroupmajor &lt;major&gt; &lt;group_id&gt;</code>",
            parse_mode=ParseMode.HTML
        )

    try:
        major = normalize_major_strict(context.args[0])
    except RollNumberError as e:
        return await update.message.reply_text(f"❌ {html.escape(str(e))}", parse_mode=ParseMode.HTML)

    if len(context.args) >= 2:
        try:
            gid = int(context.args[1])
        except ValueError:
            return await update.message.reply_text("❌ Group ID သည် ဂဏန်းဖြစ်ရပါမည်။")
    else:
        if update.effective_chat.type not in ("group", "supergroup"):
            return await update.message.reply_text("⚠️ Private chat တွင် သုံးပါက Group ID ထည့်ပေးပါ။")
        gid = update.effective_chat.id

    if gid in CLASS_GROUP_IDS:
        return await update.message.reply_text(
            "⛔ ဒီ Group ကို CLASS_GROUP_ID shared group အဖြစ် သတ်မှတ်ထားသောကြောင့် Major routing မလုပ်နိုင်ပါ။\n"
            "Task/Tutorial/Class Reminder အတွက် သက်ဆိုင်ရာ *_MAJOR GP ID ကို .env ထဲတွင် သတ်မှတ်ပါ။"
        )

    db = get_db()
    await db.group_routes.update_one(
        {"group_id": gid},
        {"$set": {"primary_major": major}, "$addToSet": {"majors": major}},
        upsert=True
    )

    await update.message.reply_text(
        f"✅ Group (<code>{gid}</code>) အား <b>[{major.upper()}]</b> ၏ Authoritative Major Group အဖြစ် သတ်မှတ်ပြီးပါပြီ။",
        parse_mode=ParseMode.HTML
    )


async def reconcilerolls_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Runs safe legacy roll-number reconciliation.
    Usage:
      /reconcilerolls          -> Dry-run verification report
      /reconcilerolls apply    -> Apply verified reconciliation
    """
    if not is_super_admin(update.effective_user.id):
        return

    apply_mode = bool(context.args and context.args[0].lower() == "apply")
    report = await reconcile_legacy_student_records(dry_run=not apply_mode)

    mode_label = "APPLIED" if apply_mode else "DRY-RUN PREVIEW"
    msg = (
        f"🛠️ <b>LEGACY ROLL RECONCILIATION ({mode_label})</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"• စစ်ဆေးခဲ့သော ကျောင်းသားစုစုပေါင်း: <b>{report['total_inspected']}</b>\n"
        f"• အောင်မြင်စွာ ပြောင်းလဲနိုင်သော စာရင်း: <b>{report['ready_or_migrated_count']}</b>\n"
        f"• Duplicate ဖြစ်နေသော Roll Slots: <b>{report['duplicate_slots_count']}</b>\n"
        f"• စိစစ်ရန်ကျန်သော (Unresolved) စာရင်း: <b>{report['unresolved_count']}</b>\n"
    )

    if report["duplicates"]:
        msg += "\n⚠️ <b>Duplicate Roll Numbers:</b>\n"
        for d in report["duplicates"][:5]:
            msg += f"  • [{d['major'].upper()}] Roll {d['roll_number']:02d}: {len(d['document_ids'])} records\n"

    if report["unresolved"]:
        msg += "\n❌ <b>Unresolved Records:</b>\n"
        for u in report["unresolved"][:5]:
            msg += f"  • <code>{html.escape(str(u['roll_no']))}</code> ({html.escape(str(u['name']))}): {html.escape(u['reason'])}\n"

    if not apply_mode:
        msg += "\n💡 <i>အမှန်တကယ် ပြောင်းလဲသိမ်းဆည်းလိုပါက <code>/reconcilerolls apply</code> ဟု ရိုက်ပါ။</i>"

    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)


async def addec_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_super_admin(update.effective_user.id):
        return

    if len(context.args) < 3:
        return await update.message.reply_text(
            "⚠️ <b>အသုံးပြုနည်း:</b> <code>/addec &lt;major&gt; &lt;nickname&gt; &lt;telegram_id&gt;</code>\n"
            "ဥပမာ: <code>/addec ec aung 123456789</code>",
            parse_mode=ParseMode.HTML
        )

    major = normalize_major(context.args[0])
    nickname = context.args[1].strip().lower().lstrip("@")
    raw_id = context.args[2].strip()

    if not major:
        return await update.message.reply_text("❌ Major အမည် မမှန်ကန်ပါ။ (ceit, ec, ep, civil, mech, mc, archi)")
    if not raw_id.isdigit():
        return await update.message.reply_text("❌ Telegram ID သည် ဂဏန်းသီးသန့် ဖြစ်ရပါမည်။")

    target_uid = int(raw_id)
    db = get_db()

    await db.ec_roles.update_one(
        {"major": major, "nickname": nickname},
        {"$set": {
            "user_id": target_uid,
            "nickname": nickname,
            "major": major,
            "role": "ec"
        }},
        upsert=True
    )

    await update.message.reply_text(
        f"✅ <b>[{major.upper()}]</b> မေဂျာအတွက် EC <b>@{html.escape(nickname)}</b> (<code>{target_uid}</code>) ကို မှတ်ပုံတင်ပြီးပါပြီ။",
        parse_mode=ParseMode.HTML
    )


async def delec_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_super_admin(update.effective_user.id):
        return

    if len(context.args) < 2:
        return await update.message.reply_text(
            "⚠️ <b>အသုံးပြုနည်း:</b> <code>/delec &lt;major&gt; &lt;nickname&gt;</code>",
            parse_mode=ParseMode.HTML
        )

    major = normalize_major(context.args[0])
    nickname = context.args[1].strip().lower().lstrip("@")
    if not major:
        return await update.message.reply_text("❌ Major အမည် မမှန်ကန်ပါ။")

    db = get_db()
    res = await db.ec_roles.delete_one({"major": major, "nickname": nickname})
    if res.deleted_count:
        await update.message.reply_text(
            f"🗑️ <b>[{major.upper()}]</b> မေဂျာမှ EC <b>@{html.escape(nickname)}</b> ကို ဖယ်ရှားလိုက်ပါပြီ။",
            parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text("⚠️ ထို EC ကို ရှာမတွေ့ပါ။")


async def eclist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_super_admin(update.effective_user.id):
        return

    db = get_db()
    query = {}
    if context.args:
        m = normalize_major(context.args[0])
        if m:
            query["major"] = m

    roles = await db.ec_roles.find(query).sort("major", 1).to_list(length=None)
    if not roles:
        return await update.message.reply_text("📭 မှတ်ပုံတင်ထားသော EC စာရင်း မရှိသေးပါ။")

    grouped = {}
    for r in roles:
        maj = r.get("major", "ceit").upper()
        grouped.setdefault(maj, []).append(r)

    msg = "👮‍♂️ <b>REGISTERED MAJOR ECs</b>\n━━━━━━━━━━━━━━━━━━\n"
    for maj, items in grouped.items():
        msg += f"\n🏛 <b>{maj} Major:</b>\n"
        for item in items:
            msg += f"  • <b>@{html.escape(str(item.get('nickname')))}</b> — <code>{item.get('user_id')}</code>\n"

    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)