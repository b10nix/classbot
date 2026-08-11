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
GROUP_NAME_CACHE = {}

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
def get_safe_user_link(user_entity) -> str:
    """
    Universal User Link Resolver
    Telegram User Object (`update.effective_user`) နဲ့ Database Dict Snapshot
    နှစ်မျိုးလုံးက Username နဲ့ ID တွေကို အလိုအလျောက် သီးခြားခွဲထုတ်ဖတ်ပေးမည်။
    """
    if not user_entity:
        return "Unknown User"

    user_id = None
    first_name = "User Profile"
    username = None

    # 📌 1. Confession တင်ချိန် (Telegram User Object)
    if hasattr(user_entity, "id"):
        user_id = user_entity.id
        first_name = user_entity.first_name or f"User {user_id}"
        username = user_entity.username

    # 📌 2. Approve / Reject လုပ်ချိန် (Database Dict Snapshot)
    elif isinstance(user_entity, dict):
        user_id = (
            user_entity.get("sender_id") 
            or user_entity.get("tg_id") 
            or user_entity.get("user_id")
        )
        first_name = (
            user_entity.get("sender_first_name") 
            or user_entity.get("first_name") 
            or user_entity.get("name") 
            or f"User {user_id or ''}"
        )
        username = (
            user_entity.get("sender_username") 
            or user_entity.get("tg_username") 
            or user_entity.get("username")
        )

    safe_first_name = html.escape(str(first_name))

    # 📌 3. Username ရှိပါက https://t.me/ Link + Username ထုတ်ပေးမည်
    if username and str(username) != "None":
        clean_username = str(username).lstrip("@").strip()
        if clean_username and clean_username.lower() != "unknown":
            safe_username = html.escape(clean_username)
            return f'<a href="https://t.me/{safe_username}">{safe_first_name}</a> (@{safe_username})'

    # 📌 4. Username မရှိပါက tg://user?id= Deep Link ထုတ်ပေးမည်
    if user_id is not None:
        return f'<a href="tg://user?id={user_id}">{safe_first_name}</a> (No Username)'

    return safe_first_name

#================== 👤 USER IDENTITY HELPERS =================
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

# ================= GROUP NAME HELPERS =================
async def get_group_name(bot, group_id: int) -> str:
  """Group ID မှ Group Name ကို အလိုအလျောက် ဆွဲထုတ်ပေးမည့် Function"""
  if group_id in GROUP_NAME_CACHE:
    return GROUP_NAME_CACHE[group_id]

  try:
    chat = await bot.get_chat(group_id)
    group_name = chat.title or str(group_id)
    GROUP_NAME_CACHE[group_id] = group_name  # Cache ထဲ မှတ်ထားမည်
    return group_name
  except Exception as e:
    # တကယ်လို့ Group ထဲမှ Bot ထွက်သွားလျှင် သို့မဟုတ် Error တက်လျှင် ID ကိုပဲ ပြမည်
    return str(group_id)