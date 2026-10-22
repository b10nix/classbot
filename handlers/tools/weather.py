import os
import random
import httpx
import logging
from datetime import datetime, timedelta, timezone

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from database.db import get_db
from utils.constants import CLASS_GROUP_IDS

logger = logging.getLogger(__name__)

WEATHER_API_KEY = os.getenv("WEATHER_API_KEY", "")
LATITUDE = 17.1147
LONGITUDE = 96.0450
EXTREME_TEMP = 38


async def get_weather_data() -> dict | None:
    if not WEATHER_API_KEY:
        return None

    url = (
    "http://api.openweathermap.org/data/2.5/weather"
    f"?lat={LATITUDE}"
    f"&lon={LONGITUDE}"
    f"&appid={WEATHER_API_KEY}"
    "&units=metric"
    )

    async with httpx.AsyncClient(
    timeout=httpx.Timeout(10.0)
    ) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.exception("Weather API request failed")

    return None


async def get_custom_tip(weather_main: str):
    db = get_db()

    default_tips = {
        "Rain": [
            "🌧️ မိုးရွာမယ်။ ထီးယူခဲ့နော် ☂️",
            "🌧️ KDrama ထဲကလို ထီးအတူဆောင်းဖို့ မစဉ်းစားနဲ့၊ ကိုယ့်ထီးကိုယူ 😌",
            "🌧️ Laundry လုပ်ထားရင် F 🫡",
            "🌧️ ဖုန်းရေမစိုအောင်လည်း ဂရုစိုက်နော် "
        ],
        "Clear": [
            "☀️ နေပူတယ်။ SPF လိမ်းဦး 😎",
            "☀️ Sun.exe has started 🔥",
            "☀️ ရေများများသောက်နော် 🥤",
            "☀️ အပြင်ထွက်ရင် roast ဖြစ်မယ် "
        ],
        "Clouds": [
            "☁️ တိမ်ထူနေတယ် 🌚",
            "☁️ Sky: Dark Mode ON 🌑",
            "☁️ နေကတော့ ခွင့်ယူထားသလိုပဲ ",
            "☁️ မိုးရွာမရွာက mood ပေါ်မူတည်တယ် 🌥️"
        ],
        "Extreme": [
            "💀 We are cooked.",
            "🔥 ငရဲ demo version လိုပဲ",
            "🥵 Survival Mode Activated!",
            "🚨 အပြင်ထွက်ရင် သတိထားနော်!"
        ]
    }

    if db is not None:
        setting = await db.settings.find_one({"type": "weather_tips"})
        if setting:
            tips = setting.get("tips", {}).get(weather_main)
            if tips:
                if isinstance(tips, list):
                    return random.choice(tips)
                return tips

    tips = default_tips.get(weather_main, default_tips["Clear"])
    return random.choice(tips)


async def format_weather_msg():
    data = await get_weather_data()

    if not data:
        return "⚠️ ရာသီဥတု အချက်အလက် ယူလို့မရသေးပါဘူး။ API Key ကို စစ်ဆေးပါ။"

    temp = round(data["main"]["temp"])
    feels_like = round(data["main"]["feels_like"])
    temp_min = round(data["main"]["temp_min"])
    temp_max = round(data["main"]["temp_max"])

    desc = data["weather"][0]["description"].title()
    main_weather = data["weather"][0]["main"]

    if temp >= EXTREME_TEMP or feels_like >= EXTREME_TEMP:
        main_weather = "Extreme"

    tip = await get_custom_tip(main_weather)

    return (
        "🌤 <b>မှော်ဘီမြို့၏ ယနေ့ ရာသီဥတု</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🌡️ လက်ရှိအပူချိန်: <b>{temp}°C</b> (ခံစားရမှု: {feels_like}°C)\n"
        f"📈 အမြင့်ဆုံး: {temp_max}°C | 📉 အနိမ့်ဆုံး: {temp_min}°C\n"
        f"☁️ အခြေအနေ: {desc}\n\n"
        f"💡 <b>Tips:</b> <i>{tip}</i>"
    )


async def weather_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_msg_id = update.message.message_id

    msg = await format_weather_msg()
    sent_msg = await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

    context.job_queue.run_once(
        auto_delete_weather,
        60,
        chat_id=chat_id,
        data=sent_msg.message_id,
    )

    try:
        context.job_queue.run_once(
            auto_delete_weather,
            60,
            chat_id=chat_id,
            data=user_msg_id,
        )
    except Exception:
        pass


async def auto_delete_weather(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    msg_id = context.job.data

    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
    except Exception:
        pass


async def morning_weather_alert(context: ContextTypes.DEFAULT_TYPE):
    groups = list(CLASS_GROUP_IDS)

    msg = await format_weather_msg()
    final_msg = f"🌅 <b>မင်္ဂလာနံနက်ခင်းပါ</b>\n\n{msg}"

    mm_tz = timezone(timedelta(hours=6, minutes=30))
    now = datetime.now(mm_tz)

    target_time = now.replace(hour=9, minute=0, second=0, microsecond=0)
    if target_time < now:
        target_time += timedelta(days=1)

    delay_seconds = (target_time - now).total_seconds()

    for gid in groups:
        try:
            sent_msg = await context.bot.send_message(
                chat_id=gid,
                text=final_msg,
                parse_mode=ParseMode.HTML,
            )

            context.job_queue.run_once(
                auto_delete_weather,
                delay_seconds,
                chat_id=gid,
                data=sent_msg.message_id,
            )
        except Exception as e:
            logger.error(f"Weather Alert Error for {gid}: {e}")
