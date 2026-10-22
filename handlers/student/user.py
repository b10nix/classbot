# handlers/student/user.py

import html
import logging
import uuid
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode

from database.db import get_db, is_major_database_enabled
from utils.helpers import schedule_delete
from services.major_service import (
    resolve_user_major_candidates_from_groups,
    extract_major_from_roll,
    find_student_record_by_user_id,
)
from services.roll_number_service import (
    RollNumberError,
    SemesterNotConfiguredError,
    normalize_roll_input,
    get_current_semester,
    get_mm_now_str,
)
from services.registration_service import normalize_student_name, normalize_phone_number
from handlers.student.student_data import build_student_message, get_active_funds_for_major

(
    REGISTER_ROLL,
    REGISTER_MAJOR_SELECT,
    REGISTER_NAME,
    REGISTER_PHONE,
    REGISTER_HOSTEL,
    REGISTER_CONFIRM,
    REGISTER_EXISTING_ACTION,
    REGISTER_PHONE_UPDATE_CONFIRM,
) = range(80, 88)

logger = logging.getLogger(__name__)

_REGISTER_DATA_KEYS = {
    "register_mode",
    "register_roll_no",
    "register_roll_int",
    "register_roll_str",
    "register_semester",
    "register_major",
    "register_major_options",
    "register_name",
    "register_phone",
    "register_hostel",
    "register_existing_roll",
    "register_existing_major",
}

_MAJOR_LABELS = {
    "ceit": "CEIT",
    "ep": "EP",
    "ec": "EC",
    "archi": "ARCHI",
    "mc": "MC",
    "mech": "ME",
    "civil": "CIVIL",
}


def _clear_register_data(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear only this flow's state; keep unrelated handlers' user_data intact."""
    for key in _REGISTER_DATA_KEYS:
        context.user_data.pop(key, None)
    # Remove stale keys left by the previous /add conversation version.
    for key in ("reg_roll_no", "reg_roll_int", "reg_roll_str", "reg_semester", "reg_major", "reg_name"):
        context.user_data.pop(key, None)


def _major_roll_filter(major: str, roll_int: int, roll_str: str) -> dict:
    """Find a roll within a Major, including supported legacy roll representations."""
    if not is_major_database_enabled() and major == "ceit":
        major_filter = {"$or": [{"major": "ceit"}, {"major": {"$exists": False}}]}
    else:
        major_filter = {"major": major}

    return {
        "$and": [
            major_filter,
            {
                "$or": [
                    {"roll_number": roll_int},
                    {"roll_number_str": roll_str},
                    {"roll_no": {"$regex": rf"(?:^|-)0*{roll_int}$", "$options": "i"}},
                ]
            },
        ]
    }


async def _send_step(update: Update, text: str, **kwargs):
    """Edit the current prompt after a callback, otherwise send a new DM message."""
    if update.callback_query:
        return await update.callback_query.edit_message_text(text, **kwargs)
    return await update.message.reply_text(text, **kwargs)


async def _ask_name_for_major(update: Update, context: ContextTypes.DEFAULT_TYPE, major: str) -> int:
    """Validate active semester and duplicate roll, then ask for the student's name."""
    roll_int = context.user_data.get("register_roll_int")
    roll_str = context.user_data.get("register_roll_str")
    if roll_int is None or not roll_str:
        _clear_register_data(context)
        await _send_step(update, "⚠️ Registration session မရှိတော့ပါ။ `/register` ဖြင့် ပြန်စပါ။")
        return ConversationHandler.END

    try:
        semester = await get_current_semester(major, strict=True)
    except SemesterNotConfiguredError as exc:
        _clear_register_data(context)
        await _send_step(update, str(exc), parse_mode=ParseMode.HTML)
        return ConversationHandler.END
    except Exception:
        logger.exception("Could not read active semester for Major=%s", major)
        _clear_register_data(context)
        await _send_step(update, "❌ Semester setting ကို ဖတ်မရပါ။ ခဏနောက်မှ ပြန်ကြိုးစားပါ။")
        return ConversationHandler.END

    db = get_db(major=major)
    if db is None:
        _clear_register_data(context)
        await _send_step(update, "❌ Database ချိတ်ဆက်မှု မအောင်မြင်ပါ။")
        return ConversationHandler.END

    try:
        existing_roll = await db.student_info.find_one(_major_roll_filter(major, roll_int, roll_str))
    except Exception:
        logger.exception("Could not check duplicate roll for Major=%s roll=%s", major, roll_str)
        _clear_register_data(context)
        await _send_step(update, "❌ ခုံနံပါတ်ကို စစ်ဆေးရာတွင် Database error ဖြစ်နေပါသည်။ ခဏနောက်မှ ပြန်ကြိုးစားပါ။")
        return ConversationHandler.END

    if existing_roll:
        _clear_register_data(context)
        await _send_step(
            update,
            f"❌ <b>{_MAJOR_LABELS.get(major, major.upper())}</b> Major တွင် Roll Number "
            f"<code>{html.escape(roll_str)}</code> ကို စာရင်းသွင်းပြီးသား ဖြစ်နေပါသည်။\n"
            "Roll Number မှန်မမှန် သက်ဆိုင်ရာ EC ထံ စစ်ဆေးပေးပါ။",
            parse_mode=ParseMode.HTML,
        )
        return ConversationHandler.END

    context.user_data["register_major"] = major
    context.user_data["register_semester"] = semester
    context.user_data["register_mode"] = "new"
    display_major = _MAJOR_LABELS.get(major, major.upper())
    await _send_step(
        update,
        f"✅ Major: <b>{display_major}</b> · Semester {semester} အဖြစ် သတ်မှတ်ပြီးပါပြီ။\n\n"
        "<b>အဆင့် ၂/၄ — နာမည်</b>\n"
        "သင့်နာမည်ကို English လိုသာ ရိုက်ထည့်ပါ။\n"
        "ဥပမာ: <code>aung aung</code> → <code>Aung Aung</code>\n\n"
        "ပယ်ဖျက်လိုပါက /cancel ကိုနှိပ်ပါ။",
        parse_mode=ParseMode.HTML,
    )
    return REGISTER_NAME


async def user_register_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the combined /register flow; /add and /ph are no longer data-entry forms."""
    if not update.effective_user or not update.effective_chat or not update.message:
        return ConversationHandler.END

    if update.effective_chat.type != "private":
        try:
            await update.message.delete()
        except Exception:
            pass
        bot_username = getattr(context.bot, "username", None)
        dm_link = f"https://t.me/{bot_username}" if bot_username else None
        message = "⚠️ ကျောင်းသားစာရင်းသွင်းခြင်းကို Bot DM (Private Chat) မှာသာ လုပ်နိုင်ပါတယ်။"
        if dm_link:
            message += f"\n<a href='{html.escape(dm_link, quote=True)}'>ဒီနေရာကိုနှိပ်ပြီး Bot DM ကိုဖွင့်ပါ</a>။"
        try:
            sent = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=message,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            schedule_delete(context, update.effective_chat.id, [sent.message_id], 15)
        except Exception:
            logger.debug("Could not send group-to-DM registration notice", exc_info=True)
        return ConversationHandler.END

    _clear_register_data(context)
    user_id = update.effective_user.id

    # Existing identities must never be overwritten by entering another roll/name.
    # Phone changes for old records are supported here so the old /ph flow can retire.
    try:
        existing_student, existing_major = await find_student_record_by_user_id(user_id)
    except Exception:
        logger.exception("Could not locate existing student record for user=%s", user_id)
        await update.message.reply_text("❌ သင့်အချက်အလက်ကို စစ်ဆေးမရပါ။ ခဏနောက်မှ ပြန်ကြိုးစားပါ။")
        return ConversationHandler.END

    if existing_student:
        context.user_data["register_mode"] = "update_phone"
        context.user_data["register_existing_major"] = existing_major
        context.user_data["register_existing_roll"] = str(existing_student.get("roll_no") or "Unknown")
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📱 ဖုန်းနံပါတ် ပြင်မည်", callback_data="register_existing_phone")],
            [InlineKeyboardButton("❌ မလုပ်တော့ပါ", callback_data="register_cancel")],
        ])
        raw_major = str(existing_major or existing_student.get("major") or "").strip().lower()
        shown_major = _MAJOR_LABELS.get(raw_major, raw_major.upper() if raw_major else "မသိရှိ")
        await update.message.reply_text(
            "ℹ️ <b>သင့် Telegram အကောင့်ဖြင့် Student Record ရှိပြီးသားပါ။</b>\n"
            f"👤 နာမည်: <b>{html.escape(str(existing_student.get('name') or 'Unknown'))}</b>\n"
            f"🎓 Major: <b>{html.escape(shown_major)}</b>\n"
            f"🔢 Roll: <code>{html.escape(str(existing_student.get('roll_no') or 'Unknown'))}</code>\n\n"
            "ထပ်မံစာရင်းသွင်းပြီး duplicate ဖြစ်ခြင်းကို ကာကွယ်ထားပါတယ်။ ဖုန်းနံပါတ် ပြင်ရန် အောက်ကခလုတ်ကို ရွေးပါ။\n"
            "နာမည်၊ Roll သို့မဟုတ် Major ပြင်ရန် သက်ဆိုင်ရာ EC ကို ဆက်သွယ်ပါ။",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )
        return REGISTER_EXISTING_ACTION

    context.user_data["register_mode"] = "new"
    await update.message.reply_text(
        "📝 <b>ကျောင်းသားစာရင်းသွင်းခြင်း (/register)</b>\n"
        "အဆင့် ၁/၄ — ခုံနံပါတ်\n\n"
        "ခုံနံပါတ်ကို <b>ဂဏန်းသီးသန့်</b> ထည့်ပါ။ Major နဲ့ Semester ကို Bot က စစ်ဆေးသတ်မှတ်ပေးပါမယ်။\n"
        "ဥပမာ: <code>43</code>\n\n"
        "ပယ်ဖျက်လိုပါက /cancel ကိုနှိပ်ပါ။",
        parse_mode=ParseMode.HTML,
    )
    return REGISTER_ROLL


async def user_register_legacy_redirect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Explain that /add and /ph have been replaced by the interactive /register flow."""
    if not update.message or not update.effective_chat:
        return ConversationHandler.END
    if update.effective_chat.type != "private":
        try:
            await update.message.delete()
        except Exception:
            pass
        bot_username = getattr(context.bot, "username", None)
        dm_link = f"https://t.me/{bot_username}" if bot_username else None
        text = "⚠️ ကျောင်းသားစာရင်းသွင်းရန် Bot DM မှာ /register ကို အသုံးပြုပါ။"
        if dm_link:
            text += f"\n<a href='{html.escape(dm_link, quote=True)}'>Bot DM ဖွင့်ရန်</a>"
        try:
            sent = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            schedule_delete(context, update.effective_chat.id, [sent.message_id], 15)
        except Exception:
            logger.debug("Could not send legacy command notice", exc_info=True)
        return ConversationHandler.END
    await update.message.reply_text(
        "ℹ️ /add နဲ့ /ph command ဟောင်းတွေကို /register flow နဲ့ အစားထိုးထားပါတယ်။\n"
        "စာရင်းသွင်းရန် သို့မဟုတ် ရှိပြီးသားအကောင့်၏ ဖုန်းနံပါတ်ပြင်ရန် <code>/register</code> ကို အသုံးပြုပါ။",
        parse_mode=ParseMode.HTML,
    )
    return ConversationHandler.END


async def user_register_receive_roll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return ConversationHandler.END
    raw = (update.message.text or "").strip()
    if not raw.isdigit():
        await update.message.reply_text(
            "⚠️ ခုံနံပါတ်ကို ဂဏန်းသီးသန့်သာ ထည့်ပါ။ ဥပမာ <code>43</code>။",
            parse_mode=ParseMode.HTML,
        )
        return REGISTER_ROLL
    try:
        roll_int, roll_str = normalize_roll_input(raw)
    except RollNumberError as exc:
        await update.message.reply_text(f"⚠️ {html.escape(str(exc))}", parse_mode=ParseMode.HTML)
        return REGISTER_ROLL

    context.user_data["register_roll_int"] = roll_int
    context.user_data["register_roll_str"] = roll_str
    context.user_data["register_roll_no"] = roll_str

    try:
        majors, err_msg = await resolve_user_major_candidates_from_groups(
            update.effective_user.id, context.bot
        )
    except Exception:
        logger.exception("Major membership resolution failed for user=%s", update.effective_user.id)
        _clear_register_data(context)
        await update.message.reply_text("❌ Major Group membership ကို စစ်ဆေးမရပါ။ ခဏနောက်မှ ပြန်ကြိုးစားပါ။")
        return ConversationHandler.END

    if not majors:
        _clear_register_data(context)
        await update.message.reply_text(
            err_msg or "⚠️ သင့် Major ကို အတည်ပြုမရပါ။ သက်ဆိုင်ရာ EC ကို ဆက်သွယ်ပါ။",
            parse_mode=ParseMode.HTML,
        )
        return ConversationHandler.END

    context.user_data["register_major_options"] = list(majors)
    if len(majors) == 1:
        return await _ask_name_for_major(update, context, majors[0])

    keyboard_rows = []
    for idx in range(0, len(majors), 2):
        row = [
            InlineKeyboardButton(
                _MAJOR_LABELS.get(major, major.upper()),
                callback_data=f"register_major:{major}",
            )
            for major in majors[idx:idx + 2]
        ]
        keyboard_rows.append(row)
    keyboard_rows.append([InlineKeyboardButton("❌ ပယ်ဖျက်မည်", callback_data="register_cancel")])
    labels = ", ".join(_MAJOR_LABELS.get(major, major.upper()) for major in majors)
    await update.message.reply_text(
        "⚠️ သင်သည် Major GP တစ်ခုထက်ပို၍ ဝင်ထားပါသည်။\n"
        f"တွေ့ရှိသော Major များ: <b>{html.escape(labels)}</b>\n\n"
        "စာရင်းသွင်းမည့် Major ကို ရွေးချယ်ပါ။ ရွေးထားသော Major နဲ့ Roll Number ကို စစ်ဆေးပြီး ဆက်သွားပါမယ်။",
        reply_markup=InlineKeyboardMarkup(keyboard_rows),
        parse_mode=ParseMode.HTML,
    )
    return REGISTER_MAJOR_SELECT


async def user_register_major_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    major = (query.data or "").partition(":")[2].strip().lower()
    allowed = context.user_data.get("register_major_options", [])
    if major not in allowed:
        _clear_register_data(context)
        await query.edit_message_text("⚠️ Major ရွေးချယ်မှု သက်တမ်းကုန်သွားပါပြီ။ `/register` ဖြင့် ပြန်စပါ။")
        return ConversationHandler.END
    return await _ask_name_for_major(update, context, major)


async def user_register_receive_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return ConversationHandler.END
    try:
        name = normalize_student_name(update.message.text or "")
    except ValueError as exc:
        await update.message.reply_text(f"⚠️ {html.escape(str(exc))}\nနာမည်ကို English လို ပြန်ထည့်ပါ။", parse_mode=ParseMode.HTML)
        return REGISTER_NAME
    context.user_data["register_name"] = name
    await update.message.reply_text(
        "<b>အဆင့် ၃/၄ — ဖုန်းနံပါတ်</b>\n"
        "ဆက်သွယ်နိုင်တဲ့ ဖုန်းနံပါတ်ကို ထည့်ပါ။\n"
        "ဥပမာ: <code>09123456789</code> သို့မဟုတ် <code>+959123456789</code>\n\n"
        "ပယ်ဖျက်လိုပါက /cancel ကိုနှိပ်ပါ။",
        parse_mode=ParseMode.HTML,
    )
    return REGISTER_PHONE


async def user_register_existing_phone_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if context.user_data.get("register_mode") != "update_phone":
        await query.edit_message_text("⚠️ Session သက်တမ်းကုန်သွားပါပြီ။ `/register` ဖြင့် ပြန်စပါ။")
        _clear_register_data(context)
        return ConversationHandler.END
    student, _major = await find_student_record_by_user_id(update.effective_user.id)
    if not student:
        await query.edit_message_text("⚠️ Student Record ကို မတွေ့တော့ပါ။ `/register` ဖြင့် ပြန်စပါ။")
        _clear_register_data(context)
        return ConversationHandler.END
    await query.edit_message_text(
        "📱 <b>ဖုန်းနံပါတ် ပြင်ဆင်ခြင်း</b>\n"
        "ဖုန်းနံပါတ်အသစ်ကို ထည့်ပါ။ 09xxxxxxxxx သို့မဟုတ် +959xxxxxxxxx ပုံစံဖြင့် ရိုက်ပါ။\n"
        "ပယ်ဖျက်လိုပါက /cancel ကိုနှိပ်ပါ။",
        parse_mode=ParseMode.HTML,
    )
    return REGISTER_PHONE


async def user_register_receive_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return ConversationHandler.END
    try:
        phone = normalize_phone_number(update.message.text or "")
    except ValueError as exc:
        await update.message.reply_text(f"⚠️ {html.escape(str(exc))}\nဖုန်းနံပါတ်ကို ပြန်ထည့်ပါ။", parse_mode=ParseMode.HTML)
        return REGISTER_PHONE

    context.user_data["register_phone"] = phone
    if context.user_data.get("register_mode") == "update_phone":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ ဖုန်းနံပါတ် အတည်ပြုမည်", callback_data="register_update_phone_confirm")],
            [InlineKeyboardButton("❌ ပယ်ဖျက်မည်", callback_data="register_cancel")],
        ])
        await update.message.reply_text(
            "📱 <b>ဖုန်းနံပါတ် အတည်ပြုပါ</b>\n\n"
            f"ဖုန်းနံပါတ်အသစ်: <code>{html.escape(phone)}</code>\n\n"
            "မှန်ကန်ပါက အတည်ပြုပါ။",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )
        return REGISTER_PHONE_UPDATE_CONFIRM

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏢 အဆောင်နေသည်", callback_data="register_hostel:yes")],
        [InlineKeyboardButton("🏠 အဆောင်မနေပါ", callback_data="register_hostel:no")],
        [InlineKeyboardButton("❌ ပယ်ဖျက်မည်", callback_data="register_cancel")],
    ])
    await update.message.reply_text(
        "<b>အဆင့် ၄/၄ — အဆောင်အခြေအနေ</b>\n"
        "အဆောင်နေ/မနေကို ရွေးချယ်ပါ။\n\n"
        "ပယ်ဖျက်လိုပါက /cancel ကိုလည်း အသုံးပြုနိုင်ပါတယ်။",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
    )
    return REGISTER_HOSTEL


async def user_register_hostel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    value = (query.data or "").partition(":")[2]
    if value not in {"yes", "no"}:
        await query.edit_message_text("⚠️ အဆောင်ရွေးချယ်မှု မမှန်ပါ။ `/register` ဖြင့် ပြန်စပါ။")
        _clear_register_data(context)
        return ConversationHandler.END

    context.user_data["register_hostel"] = value == "yes"
    major = context.user_data.get("register_major")
    semester = context.user_data.get("register_semester")
    roll_str = context.user_data.get("register_roll_str")
    name = context.user_data.get("register_name")
    phone = context.user_data.get("register_phone")
    if not all([major, semester, roll_str, name, phone]):
        _clear_register_data(context)
        await query.edit_message_text("⚠️ Registration session သက်တမ်းကုန်သွားပါပြီ။ `/register` ဖြင့် ပြန်စပါ။")
        return ConversationHandler.END

    hostel = "အဆောင်နေသည်" if value == "yes" else "အဆောင်မနေပါ"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ အချက်အလက်အားလုံး မှန်ကန်သည်", callback_data="register_confirm")],
        [InlineKeyboardButton("❌ ပယ်ဖျက်မည်", callback_data="register_cancel")],
    ])
    await query.edit_message_text(
        "🎯 <b>စာရင်းသွင်းမည့် အချက်အလက်များကို အတည်ပြုပါ</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"👤 နာမည်: <b>{html.escape(name)}</b>\n"
        f"🏛 Major: <b>{html.escape(_MAJOR_LABELS.get(major, major.upper()))}</b>\n"
        f"📚 Semester: <b>{semester}</b>\n"
        f"🔢 Roll Number: <code>{html.escape(roll_str)}</code>\n"
        f"📞 Phone: <code>{html.escape(phone)}</code>\n"
        f"🏠 အဆောင်: <b>{hostel}</b>\n\n"
        "အချက်အလက်အားလုံး မှန်ကန်ပါက အတည်ပြုပါ။",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
    )
    return REGISTER_CONFIRM


async def user_register_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = update.effective_user.id

    if query.data == "register_update_phone_confirm":
        if context.user_data.get("register_mode") != "update_phone":
            _clear_register_data(context)
            await query.edit_message_text("⚠️ Session သက်တမ်းကုန်သွားပါပြီ။ `/register` ဖြင့် ပြန်စပါ။")
            return ConversationHandler.END
        phone = context.user_data.get("register_phone")
        if not phone:
            await query.edit_message_text("⚠️ ဖုန်းနံပါတ်မတွေ့ပါ။ `/register` ဖြင့် ပြန်စပါ။")
            _clear_register_data(context)
            return ConversationHandler.END
        try:
            student, major = await find_student_record_by_user_id(tg_id)
            if not student:
                await query.edit_message_text("❌ Student Record ကို မတွေ့တော့ပါ။ EC ထံ ဆက်သွယ်ပါ။")
                _clear_register_data(context)
                return ConversationHandler.END
            db = get_db(major=major) if major else get_db()
            if db is None:
                await query.edit_message_text("❌ Database ချိတ်ဆက်မှု မအောင်မြင်ပါ။")
                _clear_register_data(context)
                return ConversationHandler.END
            await db.student_info.update_one(
                {"_id": student["_id"]},
                {"$set": {"phone": phone, "phone_updated_at": get_mm_now_str()}},
            )
        except Exception:
            logger.exception("Could not update phone for registered user=%s", tg_id)
            await query.edit_message_text("❌ ဖုန်းနံပါတ် သိမ်းဆည်းမရပါ။ ခဏနောက်မှ ပြန်ကြိုးစားပါ။")
            _clear_register_data(context)
            return ConversationHandler.END

        await query.edit_message_text(
            "✅ <b>ဖုန်းနံပါတ် အောင်မြင်စွာ ပြင်ဆင်ပြီးပါပြီ။</b>\n"
            f"📞 ဖုန်းနံပါတ်: <code>{html.escape(phone)}</code>\n"
            "Profile ပြန်ကြည့်ရန် /myprofile ကို အသုံးပြုပါ။",
            parse_mode=ParseMode.HTML,
        )
        _clear_register_data(context)
        return ConversationHandler.END

    if query.data != "register_confirm" or context.user_data.get("register_mode") != "new":
        _clear_register_data(context)
        await query.edit_message_text("⚠️ Registration session မရှိတော့ပါ။ `/register` ဖြင့် ပြန်စပါ။")
        return ConversationHandler.END

    major = context.user_data.get("register_major")
    semester = context.user_data.get("register_semester")
    roll_int = context.user_data.get("register_roll_int")
    roll_str = context.user_data.get("register_roll_str")
    name = context.user_data.get("register_name")
    phone = context.user_data.get("register_phone")
    hostel = context.user_data.get("register_hostel")
    required_values = (major, semester, roll_int, roll_str, name, phone, hostel)
    if any(value is None or value == "" for value in required_values):
        _clear_register_data(context)
        await query.edit_message_text("⚠️ Registration အချက်အလက် မပြည့်စုံပါ။ `/register` ဖြင့် ပြန်စပါ။")
        return ConversationHandler.END

    try:
        # Recheck Telegram identity and roll immediately before saving to avoid stale-session duplicates.
        existing_student, _existing_major = await find_student_record_by_user_id(tg_id)
        if existing_student:
            _clear_register_data(context)
            await query.edit_message_text(
                "⚠️ သင့်အကောင့်ကို အခြား session မှာ စာရင်းသွင်းပြီးသွားပါပြီ။ /myprofile ကို စစ်ဆေးပါ။",
                parse_mode=ParseMode.HTML,
            )
            return ConversationHandler.END

        db = get_db(major=major)
        if db is None:
            _clear_register_data(context)
            await query.edit_message_text("❌ Database ချိတ်ဆက်မှု မအောင်မြင်ပါ။")
            return ConversationHandler.END

        duplicate = await db.student_info.find_one(_major_roll_filter(major, roll_int, roll_str))
        if duplicate:
            _clear_register_data(context)
            await query.edit_message_text(
                f"❌ {html.escape(_MAJOR_LABELS.get(major, major.upper()))} Major တွင် Roll Number "
                f"<code>{html.escape(roll_str)}</code> ကို အခြားသူက စာရင်းသွင်းပြီးပါပြီ။ EC ထံ ဆက်သွယ်ပါ။",
                parse_mode=ParseMode.HTML,
            )
            return ConversationHandler.END

        internal_id = f"stu_{uuid.uuid4().hex[:12]}"
        tg_username = update.effective_user.username
        await db.student_info.insert_one({
            "internal_id": internal_id,
            "name": name,
            "major": major,
            "current_semester": semester,
            "roll_number": roll_int,
            "roll_number_str": roll_str,
            "roll_no": roll_str,
            "roll_history": [],
            "tg_id": tg_id,
            "tg_username": tg_username,
            "hostel": bool(hostel),
            "phone": phone,
            "paid_funds": {},
            "created_at": get_mm_now_str(),
        })
    except Exception:
        logger.exception("Student registration save failed for user=%s", tg_id)
        _clear_register_data(context)
        await query.edit_message_text("❌ စာရင်းသွင်းအချက်အလက် သိမ်းဆည်းမရပါ။ ခဏနောက်မှ ပြန်ကြိုးစားပါ။")
        return ConversationHandler.END

    hostel_text = "အဆောင်နေသည်" if hostel else "အဆောင်မနေပါ"
    await query.edit_message_text(
        "🎉 <b>စာရင်းသွင်းခြင်း အောင်မြင်ပါသည်။</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"👤 နာမည်: <b>{html.escape(name)}</b>\n"
        f"🎓 Major: <b>{html.escape(_MAJOR_LABELS.get(major, major.upper()))}</b> · Semester {semester}\n"
        f"🔢 Roll Number: <code>{html.escape(roll_str)}</code>\n"
        f"📞 ဖုန်း: <code>{html.escape(phone)}</code>\n"
        f"🏠 အဆောင်: <b>{hostel_text}</b>\n"
        f"🆔 Student ID: <code>{html.escape(internal_id)}</code>\n\n"
        "Profile နှင့် Fund Status ကြည့်ရန် /myprofile ကို အသုံးပြုပါ။",
        parse_mode=ParseMode.HTML,
    )
    _clear_register_data(context)
    return ConversationHandler.END


async def user_register_cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _clear_register_data(context)
    if update.message:
        await update.message.reply_text("🚫 Registration ကို ပယ်ဖျက်လိုက်ပါပြီ။ အသစ်ပြန်စရန် /register ကို ခေါ်ပါ။")
    return ConversationHandler.END


async def user_register_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text("🚫 Registration ကို ပယ်ဖျက်လိုက်ပါပြီ။ အသစ်ပြန်စရန် `/register` ကို ခေါ်ပါ။")
    _clear_register_data(context)
    return ConversationHandler.END


async def user_register_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear abandoned registration state after ConversationHandler times out."""
    _clear_register_data(context)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            "⌛ Registration session သက်တမ်းကုန်သွားပါပြီ။ အသစ်ပြန်စရန် `/register` ကို ခေါ်ပါ။"
        )
    elif update.message:
        await update.message.reply_text(
            "⌛ Registration session သက်တမ်းကုန်သွားပါပြီ။ အသစ်ပြန်စရန် /register ကို ခေါ်ပါ။"
        )
    return ConversationHandler.END


# ================= 🪪 ကျောင်းသား ကိုယ်ပိုင် Profile နှင့် Fund Status ကြည့်ရန် (/myprofile) =================
async def user_myprofile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Allows a registered student to inspect their own canonical roll number,
    major, semester, contact info, and active fund payment status in DM.
    """
    chat = update.effective_chat
    user_id = update.effective_user.id

    # Enforce DM-only to protect student financial & phone privacy
    if chat.type != 'private':
        bot_username = context.bot.username
        dm_link = f"https://t.me/{bot_username}"
        warn_msg = await update.message.reply_text(
            f"⚠️ သင်၏ ကိုယ်ရေးအချက်အလက်နှင့် ရန်ပုံငွေစာရင်းကို Bot ၏ DM (Private Chat) တွင်သာ ကြည့်ရှုနိုင်ပါသည်။\n"
            f"👉 <a href='{dm_link}'>ဒီနေရာကိုနှိပ်၍ DM တွင် /myprofile ရိုက်ပါ</a>",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )
        schedule_delete(context, chat.id, [update.message.message_id, warn_msg.message_id], 10)
        return

    student, student_major = await find_student_record_by_user_id(user_id)
    if not student:
        await update.message.reply_text(
            "📭 <b>သင့်အကောင့်ဖြင့် ကျောင်းသားစာရင်း ပေးသွင်းထားခြင်း မရှိသေးပါ။</b>\n\n"
            "စာရင်းသွင်းရန် Bot DM တွင် အောက်ပါ command ကို ရိုက်ထည့်ပါ -\n"
            "<code>/register</code>\n"
            "ပြီးလျှင် ခုံနံပါတ်၊ English နာမည်၊ ဖုန်းနံပါတ်နှင့် အဆောင်အခြေအနေကို အဆင့်လိုက် ထည့်ပါ။",
            parse_mode=ParseMode.HTML
        )
        return

    st_major = student_major or student.get("major") or extract_major_from_roll(student.get("roll_no", ""))
    if not st_major:
        await update.message.reply_text("⚠️ သင့် Major ကို အတည်ပြု၍ မရပါ။ Admin ထံ ဆက်သွယ်ပါ။")
        return
    # Use the database that owns this student's record for any subsequent operation.
    db = get_db(major=st_major) if student_major else get_db()
    if db is None:
        await update.message.reply_text("❌ Database ချိတ်ဆက်မှု မအောင်မြင်ပါ။")
        return

    # If the student document doesn't have current_semester populated yet, check major's active setting
    if not student.get("current_semester"):
        configured_sem = await get_current_semester(st_major, strict=False)
        if configured_sem:
            student["current_semester"] = configured_sem

    active_funds = await get_active_funds_for_major(st_major)
    profile_card = build_student_message(student, active_funds, include_meta=True)

    header = "🪪 <b>STUDENT PROFILE & FUND STATUS</b>\n━━━━━━━━━━━━━━━━━━\n"
    await update.message.reply_text(header + profile_card, parse_mode=ParseMode.HTML)