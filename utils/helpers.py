import html
import random
import logging
from datetime import datetime, timezone, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode

from database.db import get_db
from utils.constants import CLASS_GROUP_IDS, VIP_USERS, RANDOM_EMOJIS
from utils.auth import is_super_admin

logger = logging.getLogger(__name__)

# ================= 🛡️ CANCEL CONVERSATION =================
async def cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text("<pre>🚫 Canceled.</pre>", parse_mode=ParseMode.HTML)
        elif update.message:
            await update.message.reply_text("<pre>🚫 Canceled.</pre>", parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Error in cancel_conv: {e}")
    return ConversationHandler.END

# ================= 👤 USER INFO HELPERS =================
def get_safe_user_link(user) -> str:
    if not user: return "Unknown User"
    user_id = user.id
    raw_first_name = user.first_name if user.first_name else f"User {user_id}"
    safe_first_name = html.escape(raw_first_name)
    user_link = f'<a href="tg://user?id={user_id}">{safe_first_name}</a>'
    
    if user.username:
        return f"{user_link} (@{user.username})"
    else:
        return f"{user_link} (No Username)"

async def get_user_identity(user_id: int) -> str:
    """User ၏ Emoji (Permanent Identity) ကို ဆွဲထုတ်ပေးရန်"""
    db = get_db()
    if db is None: return "👤"
    
    user_data = await db.user_settings.find_one({"user_id": user_id})
    if user_data:
        # User က /nickname ဖြင့် ပြင်ထားလျှင် ၎င်းကို သုံးမည်
        if "nickname" in user_data and user_data["nickname"]: return html.escape(user_data["nickname"])
        # Default Emoji ရှိလျှင် ၎င်းကို သုံးမည်
        if "emoji" in user_data and user_data["emoji"]: return user_data["emoji"]
        
    # Database တွင် လုံးဝမရှိသေးပါက (ပထမဆုံးအကြိမ်) Random Emoji ထုတ်ပေးမည်
    # မှတ်ချက် - Nickname ပြောင်းသည့်အချိန်မှသာ Duplicate (တခြားသူနှင့်တူခြင်း) ကို တင်းကျပ်စွာ စစ်ဆေးမည်
    new_emoji = random.choice(RANDOM_EMOJIS)
    await db.user_settings.update_one({"user_id": user_id}, {"$set": {"emoji": new_emoji}}, upsert=True)
    return new_emoji

# ================= 🕒 TIME HELPERS =================
def get_mm_time() -> str:
    mm_tz = timezone(timedelta(hours=6, minutes=30))
    return datetime.now(mm_tz).strftime("%Y-%m-%d %I:%M:%S %p")

# ================= 🗑️ AUTO DELETE HELPERS =================
async def auto_delete_job(context: ContextTypes.DEFAULT_TYPE):
    try: await context.bot.delete_message(chat_id=context.job.chat_id, message_id=context.job.data)
    except: pass

def schedule_delete(context: ContextTypes.DEFAULT_TYPE, chat_id: int, msg_id: int, delay: int = 300):
    context.job_queue.run_once(auto_delete_job, delay, chat_id=chat_id, data=msg_id)

# ================= 🎯 MULTI-GROUP HELPERS (SECURITY CORE) =================
async def get_allowed_groups(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> list[int]:
    """User ၏ Access Level ကို စစ်ဆေးပေးသည့် Function"""
    if user_id in VIP_USERS or is_super_admin(user_id):
        return CLASS_GROUP_IDS

    allowed = []
    for gid in CLASS_GROUP_IDS:
        try:
            member = await context.bot.get_chat_member(chat_id=gid, user_id=user_id)
            if member.status in ['member', 'administrator', 'creator', 'restricted']:
                allowed.append(gid)
        except Exception:
            pass 
    return allowed

async def build_target_keyboard(user_id: int, context: ContextTypes.DEFAULT_TYPE, prefix_send: str, prefix_cancel: str):
    allowed_groups = await get_allowed_groups(user_id, context)
    keyboard = []
    for gid in allowed_groups:
        try:
            chat = await context.bot.get_chat(gid)
            title = chat.title[:25] + "..." if len(chat.title) > 25 else chat.title
        except:
            title = f"Group ID: {gid}"
        keyboard.append([InlineKeyboardButton(f"📤 SEND TO: {title}", callback_data=f"{prefix_send}_{gid}")])
        
    if len(allowed_groups) > 1:
        keyboard.append([InlineKeyboardButton("🌍 SEND TO: ALL ALLOWED GROUPS", callback_data=f"{prefix_send}_all")])
        
    keyboard.append([InlineKeyboardButton("❌ ABORT", callback_data=prefix_cancel)])
    return InlineKeyboardMarkup(keyboard)