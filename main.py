# main.py

import os
from dotenv import load_dotenv
from telegram.ext import ApplicationBuilder
import datetime
from datetime import timezone, timedelta

# 📌 အဟောင်းတွေကို ကျော်ပြီး .env အသစ်ကိုသာ အတင်းဖတ်ခိုင်းခြင်း (Override)
load_dotenv(override=True)

# 📌 Custom Logger ကို utils မှ ခေါ်ယူခြင်း
from utils.logger import get_logger
logger = get_logger(__name__)

# 📌 .env ထဲကနေ Super Admins တွေကို တကယ်ဖတ်မိလားဆိုတာ Log ထုတ်စစ်ဆေးခြင်း
logger.info(f"🟢 Loaded Super Admins: {os.getenv('SUPER_ADMINS')}")

from database.db import connect_db

# 📌 Background Jobs အတွက် Imports
from handlers.academic.academic import tasks_reminder_job, class_reminder_job
from handlers.tools.weather import morning_weather_alert

# 📌 အသစ်ခွဲထုတ်ထားသော Handler Registration ဖိုင်ကို ခေါ်ယူခြင်း
from handlers.register import register_all_handlers

TOKEN = os.getenv("TELEGRAM_TOKEN")

async def on_startup(application):
    logger.info("Starting up Bot and connecting to Database...")
    await connect_db()

def main():
    if not TOKEN:
        logger.error("❌ TELEGRAM_TOKEN is missing!")
        return

    app = ApplicationBuilder().token(TOKEN).post_init(on_startup).build()
    
    # 🚀 Background Jobs
    app.job_queue.run_repeating(class_reminder_job, interval=60, first=10)
    app.job_queue.run_repeating(tasks_reminder_job, interval=3600, first=10)

    # 📌 Morning Auto Weather Alert
    mm_tz = timezone(timedelta(hours=6, minutes=30))
    alert_time = datetime.time(hour=7, minute=30, tzinfo=mm_tz)
    app.job_queue.run_daily(morning_weather_alert, time=alert_time, days=(0, 1, 2, 3, 4))

    # ================= 📦 REGISTER ALL HANDLERS =================
    register_all_handlers(app)

    logger.info("🟢 CLASS BOT SYSTEM ONLINE...")
    app.run_polling()

if __name__ == '__main__':
    main()