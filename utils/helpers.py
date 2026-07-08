import html
import random
import logging
from datetime import datetime, timezone, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode

from database.db import get_db
# 📌 CLASS_GROUP_IDS ကို constants မှ လှမ်းယူမည်
from utils.constants import CLASS_GROUP_IDS

logger = logging.getLogger(__name__)

# 📌 Random Emojis စာရင်း (Default Identity အတွက်)
RANDOM_EMOJIS = ["🦊", "🐼", "🐯", "🦁", "🐮", "🐷", "🐸", "🐵", "🐔", "🐧", "🦉", "🦄", "🐝", "🦋", "🐙", "🐢", "🐍", "🐳", "🐉", "🌵", "🌻", "🍎", "🍉", "🍓", "🍕", "🍔", "🍩", "⚽", "🎸", "🚗", "🚀", "💎", "🔮", "🎭", "🎨", "🤡", "👻", "👽", "👾", "🤖"]

# ================= 🛡️ CANCEL CONVERSATION =================
async def cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Conversation Handler များကို Cancel လုပ်ရန် နေရာတိုင်းတွင် သုံးမည့် Helper"""
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
    """User ၏ Name ကို အမြဲတမ်း Direct Link အဖြစ် ပြောင်းပေးမည့် Helper (Username မရှိလည်း အလုပ်လုပ်သည်)"""
    user_id = user.id
    raw_first_name = user.first_name if user.first_name else "Unknown User"
    
    # 🛡️ HTML Parser မကြောင်စေရန် Sanitize လုပ်ခြင်း (Hacker တွေရဲ့ Payload ကို တားရန်)
    safe_first_name = html.escape(raw_first_name)
    
    # 🔗 Profile ကို တိုက်ရိုက်ခေါ်သွားမည့် HTML Link
    user_link = f'<a href="tg://user?id={user_id}">{safe_first_name}</a>'
    
    if user.username:
        return f"{user_link} (@{user.username})"
    else:
        return f"{user_link} (No Username)"

async def get_user_identity(user_id: int) -> str:
    """User ၏ Nickname သို့မဟုတ် Emoji ကို ဆွဲထုတ်ပေးရန်"""
    db = get_db()
    if db is None: return f"#{str(user_id)[-4:]}"
    
    user_data = await db.user_settings.find_one({"user_id": user_id})
    if user_data:
        if "nickname" in user_data and user_data["nickname"]: return html.escape(user_data["nickname"])
        if "emoji" in user_data and user_data["emoji"]: return user_data["emoji"]
        
    new_emoji = random.choice(RANDOM_EMOJIS)
    await db.user_settings.update_one({"user_id": user_id}, {"$set": {"emoji": new_emoji}}, upsert=True)
    return new_emoji

# ================= 🕒 TIME HELPERS =================
def get_mm_time() -> str:
    """မြန်မာစံတော်ချိန်ကို ယူရန် Helper"""
    mm_tz = timezone(timedelta(hours=6, minutes=30))
    return datetime.now(mm_tz).strftime("%Y-%m-%d %I:%M:%S %p")

# ================= 🗑️ AUTO DELETE HELPERS =================
async def auto_delete_job(context: ContextTypes.DEFAULT_TYPE):
    """Job Queue မှတစ်ဆင့် Message များကို အလိုအလျောက်ဖျက်ပေးမည့် Task"""
    try: 
        await context.bot.delete_message(chat_id=context.job.chat_id, message_id=context.job.data)
    except: 
        pass

def schedule_delete(context: ContextTypes.DEFAULT_TYPE, chat_id: int, msg_id: int, delay: int = 300):
    """သတ်မှတ်ထားသော အချိန် (စက္ကန့်) အကြာတွင် Message ကို ဖျက်ရန် ညွှန်ကြားခြင်း"""
    context.job_queue.run_once(auto_delete_job, delay, chat_id=chat_id, data=msg_id)

# ================= 🎯 MULTI-GROUP HELPERS =================
async def get_allowed_groups(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> list[int]:
    """User ပါဝင်သော Group များကိုသာ စစ်ထုတ်ပေးသည့် Function"""
    allowed = []
    for idx, gid in enumerate(CLASS_GROUP_IDS):
        if idx == 2: # တတိယမြောက် Group အတွက် Membership စစ်ဆေးခြင်း
            try:
                member = await context.bot.get_chat_member(chat_id=gid, user_id=user_id)
                if member.status in ['member', 'administrator', 'creator', 'restricted']:
                    allowed.append(gid)
            except Exception:
                pass 
        else:
            allowed.append(gid)
    return allowed

async def build_target_keyboard(user_id: int, context: ContextTypes.DEFAULT_TYPE, prefix_send: str, prefix_cancel: str):
    """စာပို့မည့် Group များကို ရွေးချယ်နိုင်သော Keyboard တည်ဆောက်ပေးသည့် Function"""
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