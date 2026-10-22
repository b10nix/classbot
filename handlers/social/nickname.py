import html
import re
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode

from database.db import get_db
from utils.helpers import cancel_conv, schedule_delete
from utils.constants import FOOTER, VIP_USERS
from utils.auth import is_super_admin

logger = logging.getLogger(__name__)

RECEIVE_NICKNAME = 1
CONFIRM_NICKNAME = 2


def _extract_current_identity(user_data: dict | None) -> str:
    """Helper to safely extract and format the user's current nickname/emoji."""
    if not user_data:
        return "🎲 Random Emoji (သတ်မှတ်ထားခြင်း မရှိသေးပါ)"

    current = user_data.get("nickname") or user_data.get("emoji")
    if current:
        return f"<b>{html.escape(str(current))}</b>"
    return "🎲 Random Emoji (သတ်မှတ်ထားခြင်း မရှိသေးပါ)"


async def nickname_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db = get_db()

    user_data = await db.user_settings.find_one({"user_id": user_id}) if db is not None else None
    current_identity_display = _extract_current_identity(user_data)

    # 🌟 VIP သို့မဟုတ် Super Admin ဖြစ်လျှင် True ဖြစ်မည်
    is_vip = (user_id in VIP_USERS) or is_super_admin(user_id)

    # 📌 1. VIP မဟုတ်တဲ့ သာမန် User တွေကိုသာ တစ်ကြိမ်သာ ပြင်ခွင့်ပေးမည် (Lock ချမည်)
    if not is_vip and user_data and user_data.get("nick_changed", False):
        await update.message.reply_text(
            "⛔ <b>Access Denied:</b>\n"
            f"👤 <b>သင့်ရဲ့ လက်ရှိ Identity:</b> {current_identity_display}\n\n"
            "မင်းရဲ့ Identity ကို ပြောင်းလဲပြီးသွားပါပြီ။\n"
            "System အရ Identity ကို <b>တစ်ကြိမ်သာ</b> ပြောင်းလဲခွင့်ပြုထားပါတယ်။",
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END

    # လက်ရှိ Nickname အဟောင်းကို context ထဲ ခေတ္တမှတ်ထားမည် (ထပ်တူကျမကျ စစ်ရန်)
    if user_data:
        context.user_data['old_nick'] = user_data.get("nickname") or user_data.get("emoji")
    else:
        context.user_data['old_nick'] = None

    await update.message.reply_text(
        "🎭 <b>Identity ပြောင်းလဲရန်</b>\n"
        f"👤 <b>လက်ရှိ Identity:</b> {current_identity_display}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "မင်းရဲ့ ကိုယ်ပိုင် Emoji (သို့) Nickname အသစ်ကို ရိုက်ထည့်ပါ။\n"
        "⚠️ <b>သတိပေးချက်:</b> သာမန် User များသည် <b>တစ်ကြိမ်သာ</b> ပြောင်းလဲခွင့်ရှိပါသည်။ သေချာစဉ်းစားပြီးမှ ရွေးချယ်ပါ။\n\n"
        "မလုပ်ချင်တော့ပါက /cancel ဟု ရိုက်ပါ။",
        parse_mode=ParseMode.HTML
    )
    return RECEIVE_NICKNAME


async def nickname_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    new_nick = update.message.text.strip()[:15]

    if not new_nick:
        await update.message.reply_text("⚠️ Space (အလွတ်) သီးသန့် ပေး၍မရပါ။ ပြန်လည်ရိုက်ထည့်ပါ။")
        return RECEIVE_NICKNAME

    # လက်ရှိ သုံးနေသော Nickname အဟောင်းနှင့် ထပ်တူကျနေပါက Quota မဆုံးရှုံးစေရန် တားဆီးခြင်း
    old_nick = context.user_data.get('old_nick')
    if old_nick and new_nick.lower() == str(old_nick).lower():
        await update.message.reply_text(
            f"⚠️ <b>{html.escape(new_nick)}</b> သည် သင်၏ လက်ရှိ Identity ဖြစ်နေပါသည်။\n"
            "အသစ်ပြောင်းလဲလိုပါက အခြား Identity တစ်ခု ရိုက်ထည့်ပါ (သို့မဟုတ် /cancel နှိပ်ပါ)။",
            parse_mode=ParseMode.HTML
        )
        return RECEIVE_NICKNAME

    db = get_db()

    # 📌 2. Case-Insensitive Duplicate Check (တခြားသူ ယူထားပြီးသား ဟုတ်/မဟုတ် စစ်ဆေးခြင်း)
    escaped_nick = re.escape(new_nick)
    existing_user = await db.user_settings.find_one({
        "$or": [
            {"nickname": {"$regex": f"^{escaped_nick}$", "$options": "i"}},
            {"emoji": {"$regex": f"^{escaped_nick}$", "$options": "i"}}
        ],
        "user_id": {"$ne": user_id}
    })

    if existing_user:
        await update.message.reply_text(
            f"⚠️ <b>{html.escape(new_nick)}</b> ကို တခြားသူ အသုံးပြုထားပြီးပါပြီ။\n"
            "Unique ဖြစ်ရန်အတွက် အခြား Identity တစ်ခုကို ရိုက်ထည့်ပေးပါ။",
            parse_mode=ParseMode.HTML
        )
        return RECEIVE_NICKNAME

    context.user_data['new_nick'] = new_nick
    safe_nick = html.escape(new_nick)
    old_display = html.escape(str(old_nick)) if old_nick else "🎲 Random Emoji"

    # 📌 3. Format အသစ်ဖြင့် Preview ပြသခြင်း
    preview = f"💬 <b>POST</b>\n\n<pre>ဒါကတော့ Post စာသား နမူနာဖြစ်ပါတယ်။</pre>\n\n- {safe_nick}{FOOTER}"

    kb = [
        [
            InlineKeyboardButton("✅ SAVE NICKNAME", callback_data="savenick_yes"),
            InlineKeyboardButton("❌ CANCEL", callback_data="savenick_no")
        ],
        [InlineKeyboardButton("🔄 Reset to Random Emoji", callback_data="savenick_reset")]
    ]

    is_vip = (user_id in VIP_USERS) or is_super_admin(user_id)
    warning_text = "ဒီအတိုင်း မှတ်ထားလိုက်ရမလားဗျာ?" if is_vip else "ဒီအတိုင်း <b>အတည်ပြု</b> မှတ်ထားလိုက်ရမလားဗျာ?"

    await update.message.reply_text(
        f"👀 <b>PREVIEW (နမူနာကြည့်ရန်)</b>\n"
        f"🔄 <b>ပြောင်းလဲမှု:</b> <code>{old_display}</code> ➡️ <b>{safe_nick}</b>\n\n"
        f"{preview}\n\n{warning_text}",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML
    )
    return CONFIRM_NICKNAME


async def nickname_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    db = get_db()
    user_id = update.effective_user.id

    try:
        if query.data == "savenick_yes":
            new_nick = context.user_data.get('new_nick', 'Unknown')
            if db is not None:
                # 📌 4. Save နှိပ်လျှင် nick_changed: True ဟု မှတ်မည်
                await db.user_settings.update_one(
                    {"user_id": user_id},
                    {"$set": {
                        "nickname": new_nick,
                        "emoji": new_nick,
                        "nick_changed": True
                    }},
                    upsert=True
                )

            is_vip = (user_id in VIP_USERS) or is_super_admin(user_id)
            success_note = "" if is_vip else "\n(နောက်ထပ် ပြောင်းလဲခွင့် မရှိတော့ပါ။)"

            await query.edit_message_text(
                f"✅ သင့်ရဲ့ Identity အသစ်ကို <b>{html.escape(new_nick)}</b> အဖြစ် အောင်မြင်စွာ မှတ်သားလိုက်ပါပြီ။{success_note}",
                parse_mode=ParseMode.HTML
            )

        elif query.data == "savenick_reset":
            if db is not None:
                await db.user_settings.update_one(
                    {"user_id": user_id},
                    {"$unset": {"nickname": "", "emoji": "", "nick_changed": ""}}
                )
            await query.edit_message_text("✅ ပုံမှန် Random Emoji စနစ်သို့ ပြန်လည်ပြောင်းလဲလိုက်ပါပြီ။", parse_mode=ParseMode.HTML)

        else:
            await query.edit_message_text("<pre>🚫 Canceled.</pre>", parse_mode=ParseMode.HTML)

    except Exception as e:
        logger.error(f"Error in nickname_action: {e}")
        await query.edit_message_text("<pre>❌ System Error Occurred.</pre>", parse_mode=ParseMode.HTML)

    return ConversationHandler.END


# ================= 🔓 SUPER ADMIN: NICKNAME QUOTA RESET (/resetnick) =================
async def reset_nickname_quota_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Super Admin Command to grant another nickname change chance without deleting current nicknames.
    Usage:
      /resetnick all          -> Reset lock for ALL users
      /resetnick <id/nick>    -> Reset lock for a specific user
    """
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    if not is_super_admin(user_id):
        warn = await update.message.reply_text("⛔ ဤ Command ကို Super Admin သာ အသုံးပြုခွင့်ရှိပါသည်။")
        schedule_delete(context, chat_id, warn.message_id, 5)
        return

    if not context.args:
        help_text = (
            "⚠️ <b>အသုံးပြုပုံ:</b>\n\n"
            "• အားလုံးကို ပြန်ဖွင့်ပေးရန်:\n"
            "  <code>/resetnick all</code>\n\n"
            "• တစ်ဦးချင်းစီ ပြန်ဖွင့်ပေးရန်:\n"
            "  <code>/resetnick &lt;user_id သို့မဟုတ် လက်ရှိ_nickname&gt;</code>"
        )
        await update.message.reply_text(help_text, parse_mode=ParseMode.HTML)
        return

    target = " ".join(context.args).strip()
    db = get_db()
    if db is None:
        await update.message.reply_text("❌ Database ချိတ်ဆက်မှု မအောင်မြင်ပါ။")
        return

    # Mode 1: Reset for ALL users
    if target.lower() == "all":
        res = await db.user_settings.update_many(
            {"nick_changed": True},
            {"$set": {"nick_changed": False}}
        )
        await update.message.reply_text(
            f"🔓 <b>GLOBAL NICKNAME QUOTA RESET</b>\n\n"
            f"✅ စုစုပေါင်း User (<b>{res.modified_count}</b>) ဦးအား လက်ရှိ Nickname မပျက်စေဘဲ "
            f"နောက်တစ်ကြိမ် ထပ်မံပြောင်းလဲခွင့် ပြန်ဖွင့်ပေးလိုက်ပါပြီ။",
            parse_mode=ParseMode.HTML
        )
        return

    # Mode 2: Reset for a SPECIFIC user (by Telegram ID or current Nickname)
    if target.isdigit():
        query_filter = {"user_id": int(target)}
    else:
        escaped_target = re.escape(target.lstrip("@"))
        query_filter = {
            "$or": [
                {"nickname": {"$regex": f"^{escaped_target}$", "$options": "i"}},
                {"emoji": {"$regex": f"^{escaped_target}$", "$options": "i"}}
            ]
        }

    user_doc = await db.user_settings.find_one(query_filter)
    if not user_doc:
        await update.message.reply_text(
            f"❌ <b>{html.escape(target)}</b> နှင့် သက်ဆိုင်သော Nickname မှတ်တမ်း ရှာမတွေ့ပါ။",
            parse_mode=ParseMode.HTML
        )
        return

    await db.user_settings.update_one(
        {"_id": user_doc["_id"]},
        {"$set": {"nick_changed": False}}
    )

    current_nick = user_doc.get("nickname") or user_doc.get("emoji") or "Unknown"
    target_uid = user_doc.get("user_id")

    await update.message.reply_text(
        f"🔓 <b>USER NICKNAME UNLOCKED</b>\n\n"
        f"👤 <b>Current Identity:</b> {html.escape(str(current_nick))}\n"
        f"🆔 <b>User ID:</b> <code>{target_uid}</code>\n\n"
        f"<i>နောက်တစ်ကြိမ် Nickname ထပ်မံပြောင်းလဲခွင့် ပြန်ဖွင့်ပေးပြီးပါပြီ။</i>",
        parse_mode=ParseMode.HTML
    )