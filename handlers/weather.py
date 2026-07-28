import os
import httpx
import logging
from datetime import datetime, timedelta, timezone
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from database.db import get_db

logger = logging.getLogger(__name__)

WEATHER_API_KEY = os.getenv("WEATHER_API_KEY", "")
CITY = "Hmawbi" # မှော်ဘီမြို့ကို ပုံသေ သတ်မှတ်ထားသည်

async def get_weather_data():
    if not WEATHER_API_KEY: return None
    
    # 📌 မြို့နာမည်အစား မှော်ဘီမြို့၏ တိကျသော GPS (Lat, Lon) ကို သုံး၍ ခေါ်ယူခြင်း
    url = f"http://api.openweathermap.org/data/2.5/weather?lat=17.1147&lon=96.0450&appid={WEATHER_API_KEY}&units=metric"
    
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.error(f"Weather API Error: {e}")
    return None

async def get_custom_tip(weather_main: str):
    db = get_db()
    default_tips = {
        "Rain": "မိုးရွာမယ်တဲ့ KDrama ထဲကလို ထီးအတူဆောင်းပြီး ro ဖို့မဖြစ်နိုင်တာမလို့ ကိုယ့်ထီးကိုယ်ယူခဲံ့ကြ",
        "Clear": "နေပူမယ့်နေ့ပဲ ဟူးးးး",
        "Clouds": "ရာသီဥတုက တိမ်ထူနေပါတယ်။ ဟီးဟီး🌚",
        "Extreme": "we are cooked"
    }
    
    if db is not None:
        setting = await db.settings.find_one({"type": "weather_tips"})
        if setting and weather_main in setting.get("tips", {}):
            return setting["tips"][weather_main]
    return default_tips.get(weather_main, default_tips["Clear"])

async def format_weather_msg():
    data = await get_weather_data()
    if not data: return "⚠️ ရာသီဥတု အချက်အလက် ယူလို့မရသေးပါဘူး။ API Key ကို စစ်ဆေးပါ။"

    # API မှ အချက်အလက်များ ဆွဲထုတ်ခြင်း
    temp = round(data["main"]["temp"])
    feels_like = round(data["main"]["feels_like"])
    temp_min = round(data["main"]["temp_min"])
    temp_max = round(data["main"]["temp_max"])
    desc = data["weather"][0]["description"].title()
    main_weather = data["weather"][0]["main"]
    
    # ခံစားရမည့် အပူချိန်သည် ၃၈ နှင့်အထက်ဖြစ်ပါက Extreme ဟု သတ်မှတ်မည်
    if temp >= 38 or feels_like >= 38: main_weather = "Extreme"

    tip = await get_custom_tip(main_weather)

    msg = (
        f"🌤 <b>မှော်ဘီမြို့၏ ယနေ့ ရာသီဥတု</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🌡️ လက်ရှိအပူချိန်: <b>{temp}°C</b> (ခံစားရမှု: {feels_like}°C)\n"
        f"📈 အမြင့်ဆုံး: {temp_max}°C | 📉 အနိမ့်ဆုံး: {temp_min}°C\n"
        f"☁️ အခြေအနေ: {desc}\n\n"
        f"💡 <b>Tips:</b> <i>{tip}</i>"
    )
    return msg

async def weather_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ကျောင်းသားများ /weather ဟု ရိုက်ရှာလျှင် ပြမည့် Function (Auto Delete 60s ပါဝင်သည်)"""
    chat_id = update.effective_chat.id
    user_msg_id = update.message.message_id
    
    msg = await format_weather_msg()
    sent_msg = await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
    
    # 📌 စက္ကန့် ၆၀ အကြာတွင် Bot ၏ စာနှင့် ကျောင်းသား၏ Command ကိုပါ ဖျက်မည်
    context.job_queue.run_once(auto_delete_weather, 60, chat_id=chat_id, data=sent_msg.message_id)
    try:
        context.job_queue.run_once(auto_delete_weather, 60, chat_id=chat_id, data=user_msg_id)
    except:
        pass

async def auto_delete_weather(context: ContextTypes.DEFAULT_TYPE):
    """၉ နာရီထိုးလျှင် မက်ဆေ့ချ်ကို ဖျက်မည့် နောက်ကွယ်ကစနစ်"""
    chat_id = context.job.chat_id
    msg_id = context.job.data
    try: await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
    except: pass

async def morning_weather_alert(context: ContextTypes.DEFAULT_TYPE):
    """မနက်တိုင်း Group သို့ အလိုအလျောက် ပို့ပေးမည့်စနစ်"""
    group_id_str = os.getenv("CLASS_GROUP_ID", "0")
    groups = [int(g.strip()) for g in group_id_str.split(",") if g.strip() and g.strip() != "0"]
    
    msg = await format_weather_msg()
    final_msg = f"🌅 <b>မင်္ဂလာနံနက်ခင်းပါ</b>\n\n{msg}"
    
    mm_tz = timezone(timedelta(hours=6, minutes=30))
    now = datetime.now(mm_tz)
    
    # ဒီနေ့ ၉ နာရီ တိတိ အချိန်ကို တွက်ချက်ခြင်း
    target_time = now.replace(hour=9, minute=0, second=0, microsecond=0)
    if target_time < now: target_time += timedelta(days=1)
    
    delay_seconds = (target_time - now).total_seconds()
    
    for gid in groups:
        try:
            sent_msg = await context.bot.send_message(chat_id=gid, text=final_msg, parse_mode=ParseMode.HTML)
            # မနက် ၉ နာရီရောက်ရန် ကျန်သည့် စက္ကန့်အရေအတွက် အတိအကျဖြင့် Auto Delete ကို Schedule ဆွဲခြင်း
            context.job_queue.run_once(auto_delete_weather, delay_seconds, chat_id=gid, data=sent_msg.message_id)
        except Exception as e:
            logger.error(f"Weather Alert Error for {gid}: {e}")