import os
import logging
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from database.db import get_db

logger = logging.getLogger(__name__)

# ================= 🗑 Auto Delete စနစ် =================
async def auto_delete_job(context: ContextTypes.DEFAULT_TYPE):
    """JobQueue မှ ခေါ်သုံးမည့် Auto Delete ဖန်ရှင်"""
    try:
        await context.bot.delete_message(chat_id=context.job.chat_id, message_id=context.job.data)
    except Exception:
        pass

def schedule_delete(context, chat_id, msg_id, delay):
    """သတ်မှတ်ထားသော စက္ကန့်အကြာတွင် ဖျက်ရန် Schedule ဆွဲပေးမည်"""
    context.job_queue.run_once(auto_delete_job, delay, chat_id=chat_id, data=msg_id)

# ================= 🛡️ Super Admin စစ်ဆေးခြင်း =================
def is_super_admin(user_id: int) -> bool:
    """Owner (သို့) SUPER_ADMINS များသာ ငွေကြေးပိုင်းကို ကိုင်တွယ်ခွင့်ပြုမည်"""
    owner_id = int(os.getenv("OWNER_ID", "0"))
    if user_id == owner_id: return True
    sa_str = os.getenv("SUPER_ADMINS", "")
    if sa_str:
        try:
            return user_id in [int(x.strip()) for x in sa_str.split(",") if x.strip()]
        except ValueError: pass
    return False

# ================= 💰 Fund Commands =================
async def add_fund(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Group အတွင်း ငွေပေါင်းထည့်ရန် Command (/addfund 5000)"""
    # 📌 Super Admin သာ လုပ်ခွင့်ရှိမည်
    if not is_super_admin(update.effective_user.id):
        return

    args = context.args
    if len(args) < 1:
        await update.message.reply_text("⚠️ <b>အသုံးပြုနည်း:</b> <code>/addfund 5000</code>", parse_mode=ParseMode.HTML)
        return

    try:
        amount = int(args[0])
    except ValueError:
        await update.message.reply_text("⚠️ ဂဏန်းသာ ထည့်ပါ။ ဥပမာ: <code>/addfund 5000</code>", parse_mode=ParseMode.HTML)
        return

    db = get_db()
    if db is None: return

    # MongoDB တွင် လက်ရှိငွေပေါ်သို့ သွားပေါင်းထည့်မည် ($inc)
    await db.funds.update_one(
        {"_id": "class_fund"},
        {"$inc": {"total": amount}},
        upsert=True
    )

    record = await db.funds.find_one({"_id": "class_fund"})
    total = record.get("total", 0)

    msg = (
        "💰 <b>အတန်း ရန်ပုံငွေ စာရင်း (Global)</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"📥 စုစုပေါင်း ဝင်ငွေ: {total:,} ကျပ်"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

async def clear_fund(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Group အတွင်း ရန်ပုံငွေစာရင်း အကုန်ဖျက်ရန် Command (/clearfund)"""
    # 📌 Super Admin သာ လုပ်ခွင့်ရှိမည်
    if not is_super_admin(update.effective_user.id):
        return

    db = get_db()
    if db is None: return

    # စုစုပေါင်းငွေကို သုည (0) သို့ ပြန်ပြောင်းမည်
    await db.funds.update_one(
        {"_id": "class_fund"},
        {"$set": {"total": 0}},
        upsert=True
    )

    await update.message.reply_text("✅ <b>ရန်ပုံငွေစာရင်း အားလုံးကို ရှင်းလင်းလိုက်ပါပြီ။</b>", parse_mode=ParseMode.HTML)

async def check_fund(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Group အတွင်း ကျောင်းသားများ ဝင်ငွေကြည့်ရန် Command (/fund)"""
    chat_id = update.effective_chat.id
    user_msg_id = update.message.message_id

    db = get_db()
    if db is None: return

    record = await db.funds.find_one({"_id": "class_fund"})
    total = record.get("total", 0) if record else 0

    msg = (
        "💰 <b>အတန်း ရန်ပုံငွေ စာရင်း (Global)</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"📥 စုစုပေါင်း ဝင်ငွေ: {total:,} ကျပ်"
    )
    sent_msg = await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

    # 👈 [Auto Delete] - User ရိုက်သော /fund စာ နှင့် Bot ပြန်သောစာ နှစ်ခုလုံးကို (၈၀) စက္ကန့်အကြာတွင် ဖျက်မည်
    schedule_delete(context, chat_id, user_msg_id, 80)
    schedule_delete(context, chat_id, sent_msg.message_id, 80)