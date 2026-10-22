"""Role-aware inline menu for OMNI-NET TERMINAL V4.5 (Plumber).

Public users see only general features, registered Major ECs see the EC command
center, and Super Admin controls are rendered only for authenticated Super Admins.
Owner-only controls have an additional OWNER_ID check and are visible only to the owner.
"""
from __future__ import annotations

import asyncio
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from utils.auth import is_owner, is_super_admin
from utils.helpers import schedule_delete
from services.major_service import is_major_authorized

logger = logging.getLogger(__name__)

MAIN_TEXT = (
    "<b>🟢 OMNI-NET TERMINAL</b>  <code>V4.5</code>\n"
    "<i>PLUMBER // ACADEMIC OPERATIONS SYSTEM</i>\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "<pre>TERMINAL ▸ READY\n"
    "ACCESS   ▸ ROLE-BASED\n"
    "MODULES  ▸ AVAILABLE</pre>\n"
    "<b>လိုအပ်တဲ့ module ကို ရွေးချယ်ပါ။</b>\n"
    "<i>Everything is a system. Everyone is a variable.</i>"
)


def _button(label: str, callback: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(label, callback_data=callback)


def _markup(
    rows: list[list[InlineKeyboardButton]],
    *,
    back_to: str = "back_to_main",
    back_label: str = "⬅️ Main Menu",
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([*rows, [_button(back_label, back_to)]])


async def get_main_menu_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Build the keyboard without leaking protected menu buttons to other users."""
    rows = [
        [
            _button("🎓 ACADEMIC CORE", "menu_academic"),
            _button("💬 SOCIAL HUB // COMMUNITY", "menu_social"),
        ],
        [
            _button("🛠️ UTILITIES // TOOLKIT", "menu_tools"),
            _button("🪪 STUDENT PROFILE", "menu_info"),
        ],
        [_button("⌘ COMMAND DATABASE", "menu_examples")],
    ]

    try:
        can_manage_major = await is_major_authorized(user_id)
    except Exception:
        logger.exception("Could not resolve menu permissions for user %s", user_id)
        can_manage_major = False

    # Super Admins are allowed to use Major-scoped EC operations as well.
    if can_manage_major:
        rows.append([_button("🧑‍🏫 EC OPERATIONS CENTER", "menu_ec")])

    # This button is never rendered for ordinary users or ECs.
    if is_super_admin(user_id):
        rows.append([_button("🔐 SUPER ADMIN // CONTROL CONSOLE", "menu_superadmin")])

    return InlineKeyboardMarkup(rows)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the main menu. Reply/deep-link handling remains in its own conversation handler."""
    if not update.message or not update.effective_user or not update.effective_chat:
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    user_message_id = update.message.message_id

    try:
        msg = await update.message.reply_text(
            "<pre>┌─ OMNI-NET TERMINAL V4.5 ─┐\n"
            "│ Initializing interface  │\n"
            "│ Loading access profile  │\n"
            "└─────────────────────────┘</pre>",
            parse_mode=ParseMode.HTML,
        )
        await asyncio.sleep(0.2)
        await msg.edit_text(
            MAIN_TEXT,
            reply_markup=await get_main_menu_keyboard(user_id),
            parse_mode=ParseMode.HTML,
        )
        schedule_delete(context, chat_id, [user_message_id, msg.message_id], 300)
    except Exception:
        logger.exception("Failed to display main menu for user %s", user_id)
        await update.message.reply_text(
            MAIN_TEXT,
            reply_markup=await get_main_menu_keyboard(user_id),
            parse_mode=ParseMode.HTML,
        )


async def _show_page(
    query,
    title: str,
    body: str,
    rows: list[list[InlineKeyboardButton]] | None = None,
    *,
    back_to: str = "back_to_main",
    back_label: str = "⬅️ Main Menu",
):
    text = f"<b>{title}</b>\n━━━━━━━━━━━━━━━━━━\n{body}"
    if len(text) > 3900:
        text = text[:3800] + "\n\n<i>စာသားရှည်လွန်းသဖြင့် အတိုချုံ့ထားပါတယ်။</i>"
    await query.edit_message_text(
        text,
        reply_markup=_markup(rows or [], back_to=back_to, back_label=back_label),
        parse_mode=ParseMode.HTML,
    )


async def _deny(query, message: str = "ဒီ menu ကို အသုံးပြုခွင့် မရှိပါ။"):
    await query.answer(message, show_alert=True)


async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle every menu callback and re-check access on every protected page."""
    query = update.callback_query
    if not query or not query.from_user:
        return

    user_id = query.from_user.id
    data = query.data or ""

    if data == "back_to_main":
        await query.answer()
        await query.edit_message_text(
            MAIN_TEXT,
            reply_markup=await get_main_menu_keyboard(user_id),
            parse_mode=ParseMode.HTML,
        )
        return

    # Old menu versions used menu_admin for an EC/admin page. Keep it as a safe
    # alias to the EC page; never include Super Admin commands in that response.
    if data == "menu_admin":
        data = "menu_ec"

    # Every protected callback checks authorization again; hiding a button alone
    # is not an access-control mechanism.
    if data in {
        "menu_ec", "menu_ec_academic", "menu_ec_attendance", "menu_ec_students",
        "menu_ec_finance", "menu_finance", "menu_ec_group_tools",
    }:
        try:
            if not await is_major_authorized(user_id):
                return await _deny(query, "ဒီ menu ကို အသုံးပြုခွင့် မရှိပါ။")
        except Exception:
            logger.exception("EC menu authorization check failed for user %s", user_id)
            return await _deny(query, "⛔ Permission စစ်ဆေးလို့ မရပါ။")

    if data == "menu_superadmin" or data.startswith("menu_sa_"):
        if not is_super_admin(user_id):
            return await _deny(query, "ဒီ menu ကို အသုံးပြုခွင့် မရှိပါ။")

    if data == "menu_owner":
        if not is_owner(user_id):
            return await _deny(query, "ဒီ menu ကို အသုံးပြုခွင့် မရှိပါ။")

    await query.answer()

    if data == "menu_academic":
        body = (
            "<b>အချိန်ဇယား ကြည့်ရန်</b>\n"
            "• <code>/timetable</code> သို့မဟုတ် <code>/tt</code> — မိမိ Major အလိုက် ကြည့်ရန်\n"
            "• <code>/ceit</code>, <code>/ep</code>, <code>/ec</code>, <code>/archi</code>, "
            "<code>/mc</code>, <code>/mech</code>, <code>/civil</code> — သက်ဆိုင်ရာ Major ကို ရွေးကြည့်ရန်\n"
            "• <code>/it</code> — legacy CEIT alias\n\n"
            "<b>Task / Tutorial</b>\n"
            "• <code>/tasks</code> — Task နှင့် Tutorial စာရင်း\n"
            "• <code>/tutorials</code> — Tutorial စာရင်းကို ကြည့်ရန်\n\n"
            "<b>အခြား Academic Tool</b>\n"
            "• <code>/skip</code> — အတန်းပျက်နိုင်သည့် ပမာဏ/75% attendance တွက်ရန်\n"
            "• <code>/rollcall</code> — ဆရာ/EC ပေးထားသော PIN ဖြင့် attendance မှတ်ရန်\n"
            "<i>Major မသိနိုင်ပါက သို့မဟုတ် စာရင်းမသွင်းရသေးပါက သက်ဆိုင်ရာ EC ထံ ဆက်သွယ်ပါ။</i>"
        )
        return await _show_page(query, "🎓 ACADEMIC CORE // CLASS", body)

    if data == "menu_social":
        body = (
            "<b>Confession / Post</b>\n"
            "• <code>/confess</code> — နာမည်ဖော်ပြသော confession ပို့ရန်\n"
            "• <code>/confess1</code> — Anonymous confession ပို့ရန်\n"
            "• <code>/post</code> — Post/ကြေညာချက် တင်ရန်\n"
            "• Published post ပေါ်က Reply button ကိုသုံး၍ တုံ့ပြန်နိုင်ပါတယ်။\n\n"
            "<b>Profile</b>\n"
            "• <code>/nickname</code> — မိမိ nickname သတ်မှတ်/ပြင်ဆင်ရန်\n\n"
            "<i>Bot က မေးတဲ့အဆင့်တိုင်းမှာ Cancel button ပါလာပါက ၎င်းကိုနှိပ်၍ ရပ်နိုင်ပါတယ်။</i>"
        )
        return await _show_page(query, "💬 SOCIAL HUB // COMMUNITY", body)

    if data == "menu_tools":
        body = (
            "<b>နေ့စဉ် Tool များ</b>\n"
            "• <code>/weather</code> — ရာသီဥတု အချက်အလက်\n"
            "• <code>/wifi</code> — ရရှိနိုင်သော Wi-Fi အချက်အလက်\n"
            "• <code>/status</code> — Bot/စက်အခြေအနေ\n"
            "• <code>/creator</code> — Bot အကြောင်းနှင့် Creator info\n\n"
            "<b>Text / Encoding Tools</b>\n"
            "• <code>/encrypt &lt;text&gt;</code> နှင့် <code>/decrypt &lt;text&gt;</code>\n"
            "• <code>/bin2hex &lt;binary&gt;</code> — Binary ကို Hex ပြောင်းရန်\n"
            "• <code>/hex2bin &lt;hex&gt;</code> — Hex ကို Binary ပြောင်းရန်\n\n"
            "<b>Group Admin Tools</b>\n"
            "• <code>/mention [စာသား]</code> — Group admin က member များကို mention လုပ်ရန်\n"
            "• <code>/addcmd1 &lt;trigger&gt; &lt;text&gt;</code> — Text reply command ဖန်တီးရန်\n"
            "• <code>/addcmd2 &lt;trigger&gt; &lt;text&gt;</code> — Plain text command ဖန်တီးရန်\n"
            "• <code>/addcmd3 &lt;trigger&gt; &lt;text&gt;</code> — Photo reply command ဖန်တီးရန်; photo ကို reply လုပ်ပါ\n"
            "• <code>/delcmd &lt;trigger&gt;</code> — မိမိခွင့်ရှိသော custom command ဖျက်ရန်\n"
            "<i>Group admin လုပ်ဆောင်ချက်များကို သက်ဆိုင်ရာ Group admin permission ရှိသူသာ လုပ်နိုင်ပါတယ်။</i>"
        )
        return await _show_page(query, "🛠️ UTILITIES // TOOLKIT", body)

    if data == "menu_info":
        body = (
            "<b>ကျောင်းသား စာရင်းသွင်းခြင်း</b>\n"
            "• <code>/register</code> — Roll, Major, English name, phone နှင့် hostel အချက်အလက်ကို အဆင့်လိုက် ဖြည့်ရန်\n"
            "• <code>/myprofile</code> — ကိုယ့် roll, Major နှင့် profile status ကြည့်ရန်\n\n"
            "<b>Student Search</b>\n"
            "Bot ကို Inline mode မှ ရှာဖွေရန် <code>@bot_username</code> နောက်တွင် roll number (ဥပမာ <code>01</code>) သို့မဟုတ် အမည် ရိုက်ထည့်ပါ။\n\n"
            "<i>မိမိ၏ မှန်ကန်သော Major နှင့် Roll Number ကို အသုံးပြုပါ။</i>"
        )
        return await _show_page(query, "🪪 STUDENT PROFILE // DIRECTORY", body)

    if data == "menu_examples":
        body = (
            "<b>၁။ စာရင်းသွင်းခြင်း</b>\n"
            "• <code>/register</code> — ကျောင်းသားအချက်အလက်ကို အဆင့်လိုက် စာရင်းသွင်းရန်။ Bot မေးသည့်အတိုင်း Roll, Major နဲ့ အခြားအချက်အလက်များ ဖြည့်ပါ။\n"
            "• <code>/myprofile</code> — ကိုယ့်စာရင်းသွင်းထားတဲ့ အချက်အလက်ကို ကြည့်ရန်။\n\n"
            "<b>၂။ အချိန်ဇယားနှင့် စာသင်လုပ်ငန်း</b>\n"
            "• <code>/timetable</code> သို့မဟုတ် <code>/tt</code> — ကိုယ့် Major အချိန်ဇယား ကြည့်ရန်။\n"
            "• <code>/ceit</code>, <code>/ep</code>, <code>/ec</code>, <code>/archi</code>, <code>/mc</code>, <code>/mech</code>, <code>/civil</code> — သက်ဆိုင်ရာ Major အချိန်ဇယားကို ကြည့်ရန်။\n"
            "• <code>/tasks</code> — လက်ရှိ Task နဲ့ Tutorial စာရင်း ကြည့်ရန်။\n"
            "• <code>/skip</code> — အတန်းပျက်ခွင့်ကျန်မကျန် တွက်ရန်။\n\n"
            "<b>၃။ လူမှုရေးနှင့် အထွေထွေ Tool များ</b>\n"
            "• <code>/confess</code> — နာမည်ဖော်ပြပြီး confession ပို့ရန်။\n"
            "• <code>/confess1</code> — နာမည်မဖော်ပြဘဲ confession ပို့ရန်။\n"
            "• <code>/post</code> — Post သို့မဟုတ် ကြေညာချက် တင်ရန်။\n"
            "• <code>/nickname</code> — ကိုယ့် nickname သတ်မှတ်ရန်။\n"
            "• <code>/weather</code>, <code>/wifi</code>, <code>/status</code> — သက်ဆိုင်ရာ Tool အချက်အလက် ကြည့်ရန်။\n"
            "• <code>/encrypt Hello World</code> — စာသားကို encode လုပ်ရန်။ <code>/decrypt</code> က ပြန်ဖော်ရန်။\n"
            "• <code>/bin2hex 101010</code> — Binary ကို Hex အဖြစ် ပြောင်းရန်။\n\n"
            "<b>အသုံးပြုရာမှာ မှတ်ထားရန်</b>\n"
            "• Slash (<code>/</code>) နဲ့စတဲ့ command ကို Bot chat ထဲမှာ ရိုက်ပို့ပါ။\n"
            "• Bot က မေးခွန်းအဆင့်လိုက် ပြသပါက တစ်ဆင့်ချင်း ဖြည့်ပါ။ Cancel button ပါလာလျှင် နှိပ်ပြီး ရပ်နိုင်ပါတယ်။\n"
            "• <code>/cancel</code> ကို လက်ရှိလုပ်ဆောင်ချက်က လက်ခံသည့်အခါမှသာ conversation ကို ရပ်ပေးပါမယ်။\n\n"
            "<i>EC-only commands တွေကို EC Operations Center ထဲမှာ သီးခြားဖော်ပြထားပါတယ်။</i>"
        )
        return await _show_page(query, "⌘ COMMAND GUIDE // အသုံးပြုနည်း", body)

    if data in {"menu_ec", "menu_ec_academic", "menu_ec_attendance", "menu_ec_students", "menu_ec_finance", "menu_finance", "menu_ec_group_tools"}:
        if data == "menu_ec":
            rows = [
                [_button("📚 Timetable / Task Management", "menu_ec_academic")],
                [_button("🟢 Attendance Management", "menu_ec_attendance")],
                [_button("👥 Student & EC Tools", "menu_ec_students")],
                [_button("💰 Finance & Ledger", "menu_finance")],
                [_button("👮 Group Admin / Custom Commands", "menu_ec_group_tools")],
            ]
            body = (
                "ဒီနေရာမှာ Major EC အတွက် အများဆုံးအသုံးပြုတဲ့ commands တွေကို အမျိုးအစားခွဲထားပါတယ်။\n"
                "ခွင့်ပြုချက်ကို command တစ်ခုချင်းစီမှာ ထပ်စစ်ဆေးပါတယ်။ သက်ဆိုင်ရာ Major scope ပြင်ပကို မိမိ EC role တစ်ခုတည်းနဲ့ မစီမံနိုင်ပါ။"
            )
            return await _show_page(query, "🧑‍🏫 EC OPERATIONS CENTER", body, rows)

        if data == "menu_ec_academic":
            body = (
                "<b>Timetable Management</b>\n"
                "• <code>/addclass &lt;Day&gt; &lt;Time&gt; &lt;Major&gt; &lt;Room&gt; &lt;Teacher&gt; &lt;Subject&gt;</code> — အတန်းချိန်ထည့်ရန်\n"
                "• <code>/delclass &lt;Day&gt; &lt;Time&gt; &lt;Major&gt;</code> — အတန်းချိန်တစ်ခု ဖျက်ရန်\n"
                "• <code>/clearday &lt;Day&gt; &lt;Major&gt;</code> — တစ်နေ့တာ schedule ရှင်းရန်\n\n"
                "<b>Task / Tutorial</b>\n"
                "• <code>/addtask</code> — Task အသစ်ဖန်တီးရန်\n"
                "• <code>/addtutorial</code> — Tutorial အသစ်ဖန်တီးရန်\n"
                "• <code>/edittask</code> — ရှိပြီးသား Task ပြင်ရန်\n"
                "• <code>/edittutorial</code> — ရှိပြီးသား Tutorial ပြင်ရန်\n\n"
                "Task/Tutorial wizard မှ Subject, Due Date, Due Time, reminder lead-hours နဲ့ content-ийг дарааллаар оруулна. အဆင့်တိုင်းက Inline Cancel-ээр အဆုံးлэжနိုင်ပါတယ်.\n"
                "<i>Class/Task/Tutorial ကို зөвхөн သက်ဆိုင်ရာ Major GP-дသာ ပို့သည်။ Major GP ID မသတ်မှတ်ထားပါက fallback မလုပ်ပါ။</i>"
            )
            return await _show_page(query, "📚 EC OPS // ACADEMIC", body, back_to="menu_ec", back_label="⬅️ EC Center")

        if data == "menu_ec_attendance":
            body = (
                "<b>Attendance / Roll Call</b>\n"
                "• <code>/attendance</code> — မိမိ Major အတွက် PIN အသစ်ထုတ်ပြီး Attendance session ဖွင့်ရန်\n"
                "• စတင်လိုက်သော message ရှိ <b>Close Attendance</b> button ကိုသုံး၍ session ပိတ်ရန်\n"
                "• <code>/dates</code> — Attendance date/session စာရင်း ကြည့်ရန်\n"
                "• <code>/rollcall</code> — ကျောင်းသားက PIN ဖြင့် တက်ရောက်မှု မှတ်ရန် (Public Academic menu မှလည်း ရနိုင်သည်)\n\n"
                "<i>သက်ဆိုင်ရာ Major EC သို့မဟုတ် သတ်မှတ်ထားသော approver များသာ Attendance ကို စီမံနိုင်ပါတယ်။</i>"
            )
            return await _show_page(query, "🟢 EC OPS // ATTENDANCE", body, back_to="menu_ec", back_label="⬅️ EC Center")

        if data == "menu_ec_students":
            body = (
                "<b>Student Records</b>\n"
                "• <code>/list</code> — စာရင်းသွင်းပြီး/မပြီး roll number အခြေအနေ ကြည့်ရန်\n"
                "• <code>/hostel</code> — အဆောင်နေကျောင်းသားစာရင်း ကြည့်ရန်\n"
                "• <code>/setphone &lt;RollNo&gt; &lt;Phone&gt;</code> — ကျောင်းသားဖုန်း ပြင်ရန်\n"
                "• <code>/settg &lt;RollNo&gt; &lt;Username&gt;</code> — Telegram username ချိတ်ရန်\n\n"
                "<b>EC / Fund Assignment</b>\n"
                "• <code>/assign_ec &lt;EC_Nick&gt; &lt;Roll1&gt; &lt;Roll2&gt; ...</code> — ကျောင်းသားများကို EC နဲ့ ခွဲဝေရန်\n"
                "• <code>/allocate &lt;username_or_id&gt; &lt;amount&gt;</code> — Finance collection allocation ချိန်ညှိရန်\n"
                "• <code>/ecstat</code> — EC finance dashboard/list ကြည့်ရန်\n"
                "• <code>/me</code> — မိမိ EC dashboard ကြည့်ရန်"
            )
            return await _show_page(query, "👥 EC OPS // STUDENT TOOLS", body, back_to="menu_ec", back_label="⬅️ EC Center")

        if data == "menu_finance" or data == "menu_ec_finance":
            body = (
                "<b>ရန်ပုံငွေနှင့် ငွေစာရင်း</b>\n"
                "• <code>/newfund</code> — ရန်ပုံငွေအသစ် ဖန်တီးရန်။ Bot ပြသတဲ့ အဆင့်တွေကို လိုက်လုပ်ပါ။\n"
                "• <code>/funds</code> — ရန်ပုံငွေစာရင်းကြည့်ပြီး လိုအပ်တဲ့ လုပ်ဆောင်ချက်ရွေးရန်။\n"
                "• <code>/pay</code> — ကျောင်းသား ငွေပေးသွင်းမှုကို မှတ်တမ်းတင်ရန်။\n"
                "• <code>/undo &lt;RollNo&gt;</code> — ကျောင်းသားကို ရွေးပြီး ပြန်ရုပ်သိမ်းမည့် Fund ကိုနှိပ်ပါ။ အတည်ပြုပြီးမှသာ ပြန်ရုပ်သိမ်းမည်။\n"
                "• <code>/spend &lt;ပမာဏ&gt; &lt;အကြောင်းအရာ&gt;</code> — EC သုံးစွဲငွေကို မှတ်တမ်းတင်ရန်။\n"
                "• <code>/archive_fund</code> — ရန်ပုံငွေစာရင်းမှ ရွေးပြီး ပိတ်သိမ်းစာရင်းထဲ ရွှေ့ရန်။\n"
                "• <code>/unarchive_fund</code> — ပိတ်ထားသော ရန်ပုံငွေစာရင်းမှ ရွေးပြီး ပြန်ဖွင့်ရန်။\n\n"
                "<b>ငွေစာရင်းကြည့်ရန်နှင့် ဖိုင်ထုတ်ရန်</b>\n"
                "• <code>/vault</code> — ရန်ပုံငွေဝင်ငွေ၊ သုံးငွေနှင့် လက်ကျန်ကို ကြည့်ရန်။\n"
                "• <code>/report</code> — ရန်ပုံငွေရွေးပြီး ပေးပြီးသူ/မပေးရသေးသူစာရင်း ကြည့်ရန်။\n"
                "• <code>/fundstat</code> — ရန်ပုံငွေရွေးပြီး ဘယ် EC က ဘယ်လောက်ကောက်ခံထားလဲ ကြည့်ရန်။\n"
                "• <code>/history &lt;RollNo&gt;</code> — ကျောင်းသားတစ်ဦးရဲ့ ငွေပေးသွင်းမှတ်တမ်း ကြည့်ရန်။\n"
                "• <code>/remind</code> — ရန်ပုံငွေရွေး၊ ပို့မည့်လူအရေအတွက်စစ်ပြီး အတည်ပြုကာ သတိပေးစာပို့ရန်။\n"
                "• <code>/export</code> — သက်ဆိုင်ရာ Major ရဲ့ ကျောင်းသားစာရင်းကို ဖိုင်ထုတ်ရန်။\n"
                "• <code>/exportfund</code> — ရန်ပုံငွေရွေးပြီး Excel ဖိုင်ထုတ်ရန်။\n"
                "• <code>/export_finance</code> — ဝင်ငွေနဲ့ သုံးငွေစာရင်းကို CSV ဖိုင်ထုတ်ရန်။\n"
                "• <code>/semhistory</code> — အရင် semester တွေရဲ့ ငွေစာရင်းမှတ်တမ်းကို ကြည့်ရန်။\n\n"
                "<b>အန္တရာယ်ရှိသော Fund အပြီးဖျက်ခြင်း — /nuke</b>\n"
                "• <code>/nuke</code> — /funds ထဲက Active, Archived, Completed, Cancelled Fund များကို ရွေးပြီး အပြီးဖျက်ရန်။\n"
                "  ၁။ ဖျက်မယ့် Fund ကို ရွေးပြီး Fund ID, status နဲ့ ထိခိုက်မယ့် student data ကို စစ်ပါ။\n"
                "  ၂။ Request တင်ပြီး သက်ဆိုင်ရာ Major ရဲ့ အခြား active EC တစ်ဦးထံ Approval တောင်းပါ။\n"
                "  ၃။ အခြား EC approve လုပ်မှ Fund record နဲ့ သက်ဆိုင်ရာ <code>paid_funds</code> value ကို ဖယ်ရှားပါမယ်။\n"
                "  Request တင်သူကိုယ်တိုင် approve မလုပ်နိုင်ပါ။ Payment history, fund_transactions နဲ့ audit logs ကို မဖျက်ဘဲ ထိန်းသိမ်းပါမယ်။\n\n"
                "<i>Command မအောင်မြင်ပါက Roll Number, Fund ID, Major permission နဲ့ Bot ပြသတဲ့ error message ကို စစ်ပါ။</i>"
            )
            return await _show_page(query, "💰 EC — ငွေစာရင်းနှင့် ရန်ပုံငွေ", body, back_to="menu_ec", back_label="⬅️ EC Center")

        if data == "menu_ec_group_tools":
            body = (
                "<b>Group Admin / Custom Commands</b>\n"
                "• <code>/mention [စာသား]</code> — Group admins/Owner က member များကို mention လုပ်ရန်\n"
                "• <code>/addcmd1 &lt;trigger&gt; &lt;text&gt;</code> — HTML style response command\n"
                "• <code>/addcmd2 &lt;trigger&gt; &lt;text&gt;</code> — Plain text response command\n"
                "• <code>/addcmd3 &lt;trigger&gt; &lt;text&gt;</code> — Photo reply command; photo message ကို reply လုပ်ပါ\n"
                "• <code>/delcmd &lt;trigger&gt;</code> — custom command ဖျက်ရန်\n\n"
                "<i>ဒီ commands တွေမှာ Telegram Group Administrator ဖြစ်ခြင်း သို့မဟုတ် သတ်မှတ်ထားသော Super Admin permission လိုအပ်ပါတယ်။ Inline mode/Group permission မရှိရင် command ကို bot က ငြင်းပယ်နိုင်ပါတယ်။</i>"
            )
            return await _show_page(query, "👮 EC OPS // GROUP TOOLS", body, back_to="menu_ec", back_label="⬅️ EC Center")

    if data == "menu_superadmin":
        rows = [
            [_button("🏛 Major / Group Configuration", "menu_sa_major")],
            [_button("👮 EC Registry", "menu_sa_ec")],
            [_button("⏰ Automation / Data Tools", "menu_sa_automation")],
        ]
        if is_owner(user_id):
            rows.append([_button("👑 Owner-only Settings", "menu_owner")])
        body = (
            "ဤ Console ကို သတ်မှတ်ထားသော Super Admin များသာ မြင်နိုင်ပါတယ်။\n"
            "လုပ်ဆောင်ချက်တစ်ခုချင်းစီမှာလည်း permission ထပ်စစ်ဆေးထားပါတယ်။"
        )
        return await _show_page(query, "🔐 SUPER ADMIN // CONTROL CONSOLE", body, rows)

    if data == "menu_sa_major":
        body = (
            "<b>Active Major / Semester</b>\n"
            "• <code>/setmajor &lt;major&gt;</code> — မိမိ၏ Admin/Finance workspace ရွေးရန်\n"
            "• <code>/setsemester &lt;major&gt; &lt;1-12&gt; [academic_year]</code> — Major semester setting ကြည့်/ပြင်ရန်\n"
            "• <code>/endsemester &lt;semester_name&gt; &lt;starting_balance&gt; &lt;ec_name&gt;</code> — Active Major ၏ semester funds ကို non-destructive ပိတ်သိမ်းရန်\n\n"
            "<b>Group Routing</b>\n"
            "• <code>/setgroupmajor &lt;major&gt; [group_id]</code> — Group ကို Major tag ချိတ်ရန်\n"
            "• <code>/routes</code> — Configure လုပ်ထားသော Major-specific GP routing ပြင်ရန်\n\n"
            "<b>Migration Safety</b>\n"
            "• <code>/reconcilerolls</code> — Dry-run report သာကြည့်ရန်\n"
            "• <code>/reconcilerolls apply</code> — စိစစ်ပြီးမှ reconciliation အသုံးချရန်\n\n"
            "Available Major codes: <code>ceit</code>, <code>ep</code>, <code>ec</code>, <code>archi</code>, <code>mc</code>, <code>mech</code>, <code>civil</code>."
        )
        return await _show_page(query, "🏛 SUPER ADMIN // MAJOR ROUTING", body, back_to="menu_superadmin", back_label="⬅️ Super Admin Console")

    if data == "menu_sa_ec":
        body = (
            "<b>Major EC Registry</b>\n"
            "• <code>/addec &lt;major&gt; &lt;nickname&gt; &lt;telegram_id&gt;</code> — Major-specific EC registration/assignment\n"
            "• <code>/delec &lt;major&gt; &lt;nickname&gt;</code> — Major-specific EC role ဖယ်ရှားရန်\n"
            "• <code>/eclist [major]</code> — မှတ်ပုံတင်ထားသော EC များကို စာရင်းကြည့်ရန်\n\n"
            "<i>EC role များကို shared database ထဲရှိ ec_roles စာရင်းမှ စစ်ဆေးပါတယ်။ Legacy hardcoded EC mapping မရှိပါ။</i>"
        )
        return await _show_page(query, "👮 SUPER ADMIN // EC REGISTRY", body, back_to="menu_superadmin", back_label="⬅️ Super Admin Console")

    if data == "menu_sa_automation":
        body = (
            "<b>Class Reminder Automation</b>\n"
            "• <code>/autott_status</code> — Auto class reminder on/off အခြေအနေကြည့်ရန်\n"
            "• <code>/autott_off</code> — Class reminder ကို ပိတ်ရန်\n"
            "• <code>/autott_on</code> — Class reminder ကို ပြန်ဖွင့်ရန်\n"
            "<i>ဒီ toggle က Class Reminder ကိုသာ ထိန်းချုပ်ပါတယ်; Task/Tutorial reminder က သီးခြားဖြစ်ပါတယ်။</i>\n\n"
            "<b>Data Tools</b>\n"
            "• <code>/adddummy</code> — test/dummy data ထည့်ရန် (စမ်းသပ်မှုအတွက်သာ)\n"
            "• <code>/resetnick &lt;user_id|nickname|all&gt;</code> — user တစ်ဦး သို့မဟုတ် အားလုံး၏ nickname quota ကို reset လုပ်ရန်\n"
            "• <code>/export all</code> — Major အားလုံးပါဝင်သော student export\n"
            "• <code>/ecstat</code> — Major ရွေးပြီး EC finance details ကြည့်ရန်\n"
            "• <code>/semhistory</code> — Major/semester history ကြည့်ရန်"
        )
        return await _show_page(query, "⏰ SUPER ADMIN // AUTOMATION & DATA", body, back_to="menu_superadmin", back_label="⬅️ Super Admin Console")

    if data == "menu_owner":
        body = (
            "<b>Confession / Rate Limit Settings</b>\n"
            "• <code>/setcooldown</code> — Global cooldown ချိန်ညှိရန် (inline options)\n"
            "• <code>/setusercd &lt;User_ID&gt; &lt;Minutes&gt;</code> — User-specific cooldown\n"
            "• <code>/setlimit &lt;count&gt;</code> — တစ်နေ့ခွင့်ပြုမည့် limit သတ်မှတ်ရန်\n"
            "• <code>/approvers</code> — Confession approver များ ထည့်/ဖယ်ရန်\n\n"
            "<b>Group / Utility Administration</b>\n"
            "• <code>/import</code> — Reply လုပ်ထားသော CSV member list ကို import လုပ်ရန် (Group ထဲတွင် သုံးပါ)\n"
            "• <code>/addwifi &lt;SSID&gt; &lt;Password&gt;</code> / <code>/delwifi &lt;SSID&gt;</code> — Wi-Fi record စီမံရန်\n"
            "• <code>/addegg &lt;UserID&gt; &lt;Message&gt;</code> / <code>/delegg &lt;UserID&gt;</code> — Easter egg response စီမံရန်\n"
            "• Custom emoji message တစ်ခုကို ပို့/forward လုပ်ပါ — emoji ID ကို ပြန်ပေးပါမယ်။\n\n"
            "<i>ဤ Owner-only စာမျက်နှာကို OWNER_ID ပိုင်ရှင်တစ်ဦးတည်းသာ ဖွင့်နိုင်ပါတယ်။</i>"
        )
        return await _show_page(query, "👑 OWNER ACCESS // SYSTEM SETTINGS", body, back_to="menu_superadmin", back_label="⬅️ Super Admin Console")

    # Unknown/stale callback data does not reveal any privileged help text.
    return await _show_page(
        query,
        "ℹ️ MENU",
        "ဒီ menu button သက်တမ်းကုန်သွားနိုင်ပါတယ်။ Main Menu ကို ပြန်ဖွင့်ပြီး ထပ်ရွေးပါ။",
    )
