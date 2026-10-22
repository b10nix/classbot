# handlers/academic/skip.py

import calendar
from datetime import datetime, timezone, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode

from database.db import get_db
from utils.constants import get_default_majors_for_group
from utils.helpers import schedule_delete
from services.major_service import (
    normalize_major,
    extract_major_from_roll,
    resolve_user_major_from_groups
)

SKIP_MAJOR, SKIP_SUBJECT, SKIP_MISSED = range(50, 53)


def parse_periods(time_str: str) -> int:
    """09:00-11:50 ကဲ့သို့သော အချိန်များကို ၃ ချိန်ဟု အလိုအလျောက် တွက်ထုတ်ပေးသည်"""
    try:
        start_str, end_str = time_str.split('-')
        sh, sm = map(int, start_str.split(':'))
        eh, em = map(int, end_str.split(':'))
        duration = (eh * 60 + em) - (sh * 60 + sm)
        return (duration + 10) // 60
    except Exception:
        return 0


def get_month_day_counts() -> tuple[dict, str]:
    """ဒီလထဲတွင် တနင်္လာမှ တနင်္ဂနွေ ဘယ်နှရက်စီပါသလဲ တွက်ထုတ်ခြင်း"""
    mm_tz = timezone(timedelta(hours=6, minutes=30))
    now = datetime.now(mm_tz)
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    counts = {day: 0 for day in day_names}
    cal = calendar.monthcalendar(now.year, now.month)
    for week in cal:
        for i, day in enumerate(week):
            if day != 0:
                counts[day_names[i]] += 1
    return counts, now.strftime("%B %Y")


def _get_timetable_query_majors(major: str) -> list[str]:
    """
    Returns both canonical and timetable alias keys so 'ceit' and 'it'
    (or 'mech' and 'me') never miss timetable documents.
    """
    norm = normalize_major(major) or major.lower()
    if norm == "ceit":
        return ["ceit", "it"]
    if norm == "mech":
        return ["mech", "me"]
    return [norm]


def _build_major_selection_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("💻 CEIT / IT", callback_data="skipmaj_ceit"),
            InlineKeyboardButton("📡 EC", callback_data="skipmaj_ec")
        ],
        [
            InlineKeyboardButton("🏗️ Civil", callback_data="skipmaj_civil"),
            InlineKeyboardButton("⚡ EP", callback_data="skipmaj_ep")
        ],
        [
            InlineKeyboardButton("⚙️ ME", callback_data="skipmaj_mech"),
            InlineKeyboardButton("🤖 MC", callback_data="skipmaj_mc")
        ],
        [InlineKeyboardButton("🏛️ Archi", callback_data="skipmaj_archi")],
        [InlineKeyboardButton("❌ Cancel", callback_data="skip_cancel")]
    ]
    return InlineKeyboardMarkup(keyboard)


async def _detect_student_major_for_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str | None:
    """
    Auto-detects the student's major in priority order:
    1. Explicit argument passed to /skip (e.g. `/skip ec`)
    2. Registered student profile in `db.student_info`
    3. Current chat's configured Major-specific GP ID
    4. Authoritative membership across configured Major groups
    Returns canonical major string or None if unknown/ambiguous.
    """
    if context.args:
        explicit = normalize_major(context.args[0])
        if explicit:
            return explicit

    db = get_db()
    if db is None:
        return None

    user_id = update.effective_user.id

    # 1. Check registered student record
    student = await db.student_info.find_one({"$or": [{"tg_id": user_id}, {"user_id": user_id}]})
    if student:
        st_major = normalize_major(student.get("major")) or extract_major_from_roll(
            student.get("roll_no", ""), default=None
        )
        if st_major:
            return st_major

    # 2. Only a GP explicitly listed under a Major environment variable can identify a Major.
    chat = update.effective_chat
    if chat and chat.type in ("group", "supergroup"):
        configured = get_default_majors_for_group(chat.id)
        if len(configured) == 1:
            return next(iter(configured))

    # 3. Check user's verified group memberships across Major-specific groups
    resolved_major, _ = await resolve_user_major_from_groups(user_id, context.bot)
    return resolved_major


async def _render_subject_selection(
    major: str,
    context: ContextTypes.DEFAULT_TYPE,
    auto_detected: bool = False
) -> tuple[bool, str, InlineKeyboardMarkup | None]:
    """
    Queries subjects from `db.timetable` for the given major and builds the subject keyboard.
    Returns: (has_subjects, message_html, reply_markup)
    """
    db = get_db()
    if db is None:
        return False, "⚠️ Database ချိတ်ဆက်မှု အဆင်မပြေပါ။", None

    tt_majors = _get_timetable_query_majors(major)
    timetables = await db.timetable.find({"major": {"$in": tt_majors}}).to_list(length=None)

    subjects = set()
    for tt in timetables:
        for p in tt.get("periods", []):
            subj = p.get("subject", "").strip()
            if subj and "self study" not in subj.lower() and "lunch" not in subj.lower():
                subjects.add(subj)

    if not subjects:
        kb = [
            [InlineKeyboardButton("🔄 အခြား Major ရွေးရန်", callback_data="skip_change_major")],
            [InlineKeyboardButton("❌ Cancel", callback_data="skip_cancel")]
        ]
        return (
            False,
            f"⚠️ <b>{major.upper()}</b> Major အတွက် Database ထဲတွင် အတန်းချိန်/ဘာသာရပ်များ မရှိသေးပါ။",
            InlineKeyboardMarkup(kb)
        )

    sorted_subjects = sorted(subjects)
    subject_map = {str(i): subj for i, subj in enumerate(sorted_subjects)}
    context.user_data['skip_major'] = major
    context.user_data['skip_subjects_map'] = subject_map

    keyboard = []
    for idx, subj in subject_map.items():
        btn_text = subj[:35] + "..." if len(subj) > 35 else subj
        keyboard.append([InlineKeyboardButton(f"📘 {btn_text}", callback_data=f"skipsub_{idx}")])

    keyboard.append([
        InlineKeyboardButton("🔄 Change Major", callback_data="skip_change_major"),
        InlineKeyboardButton("❌ Cancel", callback_data="skip_cancel")
    ])

    detect_badge = " <i>(Auto-Detected)</i>" if auto_detected else ""
    text = (
        f"🧮 <b>75% SURVIVAL CALCULATOR</b>\n"
        f"🏛 <b>Major:</b> <b>{major.upper()}</b>{detect_badge}\n\n"
        f"တွက်ချက်လိုသော <b>ဘာသာရပ်ကို ရွေးချယ်ပါ:</b>"
    )
    return True, text, InlineKeyboardMarkup(keyboard)


# ====== 🧮 CONVERSATION HANDLERS ======
async def skip_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /skip စတင်သည့်အခါ ကျောင်းသား၏ Major ကို Auto-detect လုပ်၍ ဘာသာရပ်များကို တန်းပြမည်။
    Major မသိရသေးသူများကိုသာ Major ရွေးခိုင်းမည်။
    """
    chat_id = update.effective_chat.id
    user_msg_id = update.message.message_id

    detected_major = await _detect_student_major_for_skip(update, context)
    if detected_major:
        has_subj, text, markup = await _render_subject_selection(
            detected_major, context, auto_detected=True
        )
        msg = await update.message.reply_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
        schedule_delete(context, chat_id, [user_msg_id, msg.message_id], 60)
        return SKIP_SUBJECT if has_subj else SKIP_MAJOR

    msg = await update.message.reply_text(
        "🧮 <b>75% SURVIVAL CALCULATOR</b>\n\n"
        "ဘာသာရပ်တစ်ခုချင်းစီအတွက် ၇၅% ပြည့်/မပြည့်ကို တွက်ချက်ပေးပါမည်။\n"
        "<b>သင်၏ Major ကို ရွေးချယ်ပါ:</b>",
        reply_markup=_build_major_selection_keyboard(),
        parse_mode=ParseMode.HTML
    )
    schedule_delete(context, chat_id, [user_msg_id, msg.message_id], 60)
    return SKIP_MAJOR


async def skip_process_major(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Major ရွေးပြီးနောက် ဘာသာရပ်များကို Database မှ ဆွဲထုတ်ပြမည်"""
    query = update.callback_query
    await query.answer()

    if query.data == "skip_cancel":
        await query.edit_message_text("🚫 တွက်ချက်ခြင်းကို ရပ်ဆိုင်းလိုက်ပါပြီ။")
        return ConversationHandler.END

    if query.data == "skip_change_major":
        await query.edit_message_text(
            "🧮 <b>75% SURVIVAL CALCULATOR</b>\n\n<b>သင်၏ Major ကို ရွေးချယ်ပါ:</b>",
            reply_markup=_build_major_selection_keyboard(),
            parse_mode=ParseMode.HTML
        )
        return SKIP_MAJOR

    raw_major = query.data.split("_")[1]
    major = normalize_major(raw_major) or raw_major

    has_subj, text, markup = await _render_subject_selection(major, context, auto_detected=False)
    await query.edit_message_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
    return SKIP_SUBJECT if has_subj else SKIP_MAJOR


async def skip_process_subject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ဘာသာရပ်ရွေးပြီးလျှင် ထိုဘာသာရပ်၏ အတန်းချိန်ကို တွက်ချက်မည်"""
    query = update.callback_query
    await query.answer()

    if query.data == "skip_cancel":
        await query.edit_message_text("🚫 တွက်ချက်ခြင်းကို ရပ်ဆိုင်းလိုက်ပါပြီ။")
        return ConversationHandler.END

    if query.data == "skip_change_major":
        await query.edit_message_text(
            "🧮 <b>75% SURVIVAL CALCULATOR</b>\n\n<b>သင်၏ Major ကို ရွေးချယ်ပါ:</b>",
            reply_markup=_build_major_selection_keyboard(),
            parse_mode=ParseMode.HTML
        )
        return SKIP_MAJOR

    subj_idx = query.data.split("_")[1]
    selected_subject = context.user_data.get('skip_subjects_map', {}).get(subj_idx, "")
    major = context.user_data.get('skip_major', "")

    if not selected_subject:
        await query.edit_message_text("⚠️ Error: ဘာသာရပ်ကို ရှာမတွေ့ပါ။")
        return ConversationHandler.END

    context.user_data['skip_selected_subj'] = selected_subject

    db = get_db()
    day_counts, current_month_str = get_month_day_counts()
    tt_majors = _get_timetable_query_majors(major)
    timetables = await db.timetable.find({"major": {"$in": tt_majors}}).to_list(length=None)

    total_periods_this_month = 0
    for tt in timetables:
        day_name = tt.get("day", "")
        days_in_month = day_counts.get(day_name, 0)

        daily_periods = 0
        for p in tt.get("periods", []):
            if p.get("subject", "").strip() == selected_subject:
                daily_periods += parse_periods(p.get("time", ""))

        total_periods_this_month += (daily_periods * days_in_month)

    if total_periods_this_month == 0:
        await query.edit_message_text(
            f"⚠️ <b>{selected_subject}</b> အတွက် ဒီလထဲမှာ စာသင်ချိန် မရှိပါဘူး။",
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END

    context.user_data['skip_total'] = total_periods_this_month
    context.user_data['skip_menu_msg_id'] = query.message.message_id

    await query.edit_message_text(
        f"✅ <b>[{major.upper()}] {current_month_str}</b> အတွက် <b>{selected_subject}</b> ၏ အတန်းချိန်မှာ "
        f"<b>{total_periods_this_month} ချိန်</b> ရှိပါတယ်။\n\n"
        f"အဲ့ဒီထဲကနေ ဒီလထဲမှာ <b>ဘယ်နှချိန် ပျက်ထားပြီးပြီလဲ？</b>\n"
        f"<i>(ဂဏန်းသက်သက်သာ ရိုက်ထည့်ပါ - ဥပမာ: 2)</i>",
        parse_mode=ParseMode.HTML
    )
    return SKIP_MISSED


async def skip_receive_missed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ပျက်ထားသည့်အချိန်ကို လက်ခံပြီး ရလဒ်ထုတ်ပြမည်"""
    chat_id = update.effective_chat.id
    user_msg_id = update.message.message_id

    schedule_delete(context, chat_id, user_msg_id, 60)
    old_menu_id = context.user_data.get('skip_menu_msg_id')
    if old_menu_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=old_menu_id)
        except Exception:
            pass

    try:
        missed = int(update.message.text.strip())
        total = context.user_data.get('skip_total', 0)
        subject = context.user_data.get('skip_selected_subj', "ဘာသာရပ်")
        major = context.user_data.get('skip_major', "").upper()

        if missed < 0 or missed > total:
            err = await update.message.reply_text("❌ ပျက်တဲ့အချိန်က စုစုပေါင်းထက် များနေလို့ မရပါဘူး။ /skip ပြန်နှိပ်ပါ။")
            schedule_delete(context, chat_id, err.message_id, 60)
            return ConversationHandler.END

        max_absences = int(total * 0.25)
        remaining_safe = max_absences - missed
        current_projected_percent = round(((total - missed) / total) * 100, 1)

        report = (
            f"📊 <b>[{major}] ATTENDANCE REPORT</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📘 ဘာသာရပ်: <b>{subject}</b>\n"
            f"🔹 ဒီလ၏ စုစုပေါင်းအတန်းချိန်: <b>{total}</b> ချိန်\n"
            f"🔹 ပျက်ထားသည့် အချိန်: <b>{missed}</b> ချိန်\n\n"
            f"📈 လကုန်လျှင် ရနိုင်မည့် ရာခိုင်နှုန်း: <b>{current_projected_percent}%</b>\n\n"
            f"💡 <b>အကြံပြုချက်:</b>\n"
        )

        if remaining_safe > 0:
            report += f"🎉 ဒီဘာသာရပ်ကို နောက်ထပ် <b>({remaining_safe})</b> ချိန် အေးဆေး ထပ်ပျက်လို့ ရပါသေးတယ်။ 75% ပြည့်ဖို့ လုံလောက်ပါတယ်။"
        elif remaining_safe == 0:
            report += "⚠️ လုံးဝ (လုံးဝ) ထပ်ပျက်လို့ မရတော့ပါဘူး။ နောက်တစ်ချိန်ပျက်တာနဲ့ 75% အောက် ရောက်သွားပါမယ်။ မဖြစ်မနေ တက်ပါဗျာ။"
        else:
            report += "💀 ဂွမ်းပါပြီ... အခုချိန်ကစပြီး အကုန်တက်ရင်တောင် ဒီဘာသာအတွက် 75% မပြည့်တော့ပါဘူး။ ဆရာမဆီသွားပြီး မျက်နှာချိုသာ သွားသွေးထားပါတော့။"

        final_msg = await update.message.reply_text(report, parse_mode=ParseMode.HTML)
        schedule_delete(context, chat_id, final_msg.message_id, 60)
        return ConversationHandler.END

    except ValueError:
        err = await update.message.reply_text("❌ ဂဏန်းအမှန်သာ ထည့်ပေးပါဗျာ။ အစကနေ /skip ပြန်နှိပ်ပါ။")
        schedule_delete(context, chat_id, err.message_id, 60)
        return ConversationHandler.END


async def skip_cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_msg_id = update.message.message_id
    schedule_delete(context, chat_id, user_msg_id, 60)
    msg = await update.message.reply_text("🚫 တွက်ချက်ခြင်းကို ရပ်ဆိုင်းလိုက်ပါပြီ။")
    schedule_delete(context, chat_id, msg.message_id, 60)
    return ConversationHandler.END