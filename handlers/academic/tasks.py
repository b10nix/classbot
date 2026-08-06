import os
import logging
from datetime import datetime, timezone, timedelta
from telegram import Update
from telegram.ext import ContextTypes
from database.db import get_db

logger = logging.getLogger(__name__)
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

async def add_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin မှ Tutorial သို့ Assignment အသစ် ထည့်ရန်"""
    if update.effective_user.id != OWNER_ID:
        return

    args = context.args
    if len(args) < 4:
        usage = (
            "⚠️ <b>အသုံးပြုနည်း:</b>\n"
            "<code>/addtask <အမျိုးအစား> <YYYY-MM-DD> <ဘာသာရပ်> <အကြောင်းအရာ></code>\n\n"
            "📌 ဥပမာ (Tutorial): <code>/addtask tutorial 2026-05-25 Math Chapter_1_to_3</code>\n"
            "📌 ဥပမာ (Assignment): <code>/addtask assignment 2026-05-28 Web_Dev Project_UI</code>"
        )
        await update.message.reply_text(usage, parse_mode="HTML")
        return

    task_type = args[0].lower()
    if task_type not in ["tutorial", "assignment"]:
        task_type = "assignment"

    date_str = args[1]
    subject = args[2]
    desc = " ".join(args[3:])

    db = get_db()
    if db is None: return

    new_task = {
        "type": task_type,
        "date": date_str,
        "subject": subject,
        "description": desc
    }

    try:
        await db.tasks.insert_one(new_task)
        await update.message.reply_text(f"✅ <b>{task_type.capitalize()}</b> ကို အောင်မြင်စွာ မှတ်တမ်းတင်ပြီးပါပြီ။", parse_mode="HTML")
    except Exception as e:
        logger.error(f"Task DB Error: {e}")

async def get_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ကျောင်းသားများမှ /tutorials သို့ /tasks ဖြင့် ပြန်ကြည့်ရန်"""
    command = update.message.text.split()[0].lower()
    task_type = "tutorial" if "tutorial" in command else "assignment"

    db = get_db()
    if db is None: return

    mm_tz = timezone(timedelta(hours=6, minutes=30))
    now = datetime.now(mm_tz)
    today_str = now.strftime("%Y-%m-%d")

    # ယနေ့နှင့် ယနေ့အထက် (မရောက်သေးသော) ရက်များကိုသာ ပြမည်
    cursor = db.tasks.find({"type": task_type, "date": {"$gte": today_str}}).sort("date", 1)
    tasks = await cursor.to_list(length=100)

    title = "📝 <b>လာမည့် Assignments များ</b>" if task_type == "assignment" else "📝 <b>လာမည့် Tutorials များ</b>"
    
    if not tasks:
        await update.message.reply_text(f"{title}\n━━━━━━━━━━━━━━━━━━\n📭 လတ်တလော မရှိသေးပါ။", parse_mode="HTML")
        return

    text = f"{title}\n━━━━━━━━━━━━━━━━━━\n"
    today_clean = now.replace(hour=0, minute=0, second=0, microsecond=0)

    for t in tasks:
        target_date = datetime.strptime(t["date"], "%Y-%m-%d").replace(tzinfo=mm_tz)
        days_left = (target_date - today_clean).days

        if days_left == 0:
            left_str = "🔥 <b>ယနေ့!</b>"
        elif days_left == 1:
            left_str = "⏳ <b>မနက်ဖြန်!</b>"
        else:
            left_str = f"⏳ <b>{days_left} ရက်အလို</b>"

        text += f"📅 <b>{t['date']}</b> ({left_str})\n📚 <b>{t['subject']}</b>\n💡 {t['description']}\n\n"

    await update.message.reply_text(text, parse_mode="HTML")