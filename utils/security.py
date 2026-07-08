import logging
from datetime import datetime
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from database.db import get_db
from utils.helpers import schedule_delete

logger = logging.getLogger(__name__)

# ================= ⏳ DAILY LIMIT SYSTEM =================
async def get_daily_limit() -> int:
    """တစ်နေ့စာ အများဆုံး ပို့ခွင့်ပြုမည့် အကြိမ်အရေအတွက်ကို ဆွဲယူရန်[cite: 1]"""
    db = get_db()
    if db is None: return 3
    setting = await db.settings.find_one({"type": "daily_limit"})
    return setting.get("count", 3) if setting else 3

async def check_daily_limit(user_id: int) -> tuple[bool, int]:
    """User ၏ ယနေ့ပို့ထားသော အကြိမ်အရေအတွက်သည် Limit ပြည့်/မပြည့် စစ်ဆေးရန်[cite: 1]"""
    limit = await get_daily_limit()
    if limit == 0: return True, 0
    db = get_db()
    if db is None: return True, limit
    
    today_str = datetime.now().strftime("%Y-%m-%d")
    record = await db.daily_limits.find_one({"user_id": user_id, "date": today_str})
    
    if record and record.get("count", 0) >= limit: 
        return False, limit
    return True, limit

async def increment_daily_limit(user_id: int):
    """User စာပို့ပြီးတိုင်း ယနေ့အကြိမ်အရေအတွက်ကို (၁) တိုးရန်[cite: 1]"""
    db = get_db()
    if db is None: return
    today_str = datetime.now().strftime("%Y-%m-%d")
    await db.daily_limits.update_one(
        {"user_id": user_id, "date": today_str}, 
        {"$inc": {"count": 1}}, 
        upsert=True
    )

# ================= ⏱️ COOLDOWN / ANTI-SPAM SYSTEM =================
async def check_user_cooldown(user_id: int) -> int:
    """User တစ်ယောက် စာထပ်ပို့နိုင်ရန် စောင့်ရမည့် အချိန် (စက္ကန့်) ကို တွက်ချက်ရန်[cite: 1]"""
    db = get_db()
    if db is None: return 0
    
    # 📌 User အတွက် သီးသန့် Cooldown သတ်မှတ်ထားခြင်း ရှိ/မရှိ စစ်ဆေးမည်
    user_setting = await db.user_settings.find_one({"user_id": user_id})
    if user_setting and "custom_cooldown" in user_setting: 
        limit_mins = user_setting["custom_cooldown"]
    else:
        # 📌 မရှိပါက Global Cooldown ကို သုံးမည်
        global_setting = await db.settings.find_one({"type": "cooldown_limit"})
        limit_mins = global_setting.get("minutes", 0) if global_setting else 0
        
    if limit_mins == 0: return 0
    
    user_data = await db.user_cooldowns.find_one({"user_id": user_id})
    if not user_data: return 0
    
    last_used = user_data.get("last_used")
    if not last_used: return 0
    
    now = datetime.now()
    passed_time = (now - last_used).total_seconds()
    required_time = limit_mins * 60
    
    if passed_time < required_time: 
        return int(required_time - passed_time)
    return 0

async def update_user_cooldown(user_id: int):
    """User စာပို့ပြီးတိုင်း နောက်ဆုံးပို့ခဲ့သော အချိန်ကို မှတ်သားရန်[cite: 1]"""
    db = get_db()
    if db is None: return
    await db.user_cooldowns.update_one(
        {"user_id": user_id}, 
        {"$set": {"last_used": datetime.now()}}, 
        upsert=True
    )

async def show_cooldown_warning(update: Update, remain_sec: int, context: ContextTypes.DEFAULT_TYPE):
    """စောင့်ရမည့်အချိန်ကို User ထံ သတိပေးစာ ပို့ရန်[cite: 1]"""
    m, s = divmod(remain_sec, 60)
    h, m = divmod(m, 60)
    time_str = f"{int(h)} နာရီ {int(m)} မိနစ်" if h > 0 else f"{int(m)} မိနစ် {int(s)} စက္ကန့်"
    
    warn = await update.message.reply_text(
        f"⏳ <b>COOLDOWN ACTIVE</b>\n\nSpam ကာကွယ်ရန်အတွက် ကျေးဇူးပြု၍ <b>{time_str}</b> စောင့်ပြီးမှ ထပ်ပို့ပေးပါ။", 
        parse_mode=ParseMode.HTML
    )
    # 📌 သတိပေးစာကို ၁၅ စက္ကန့်အကြာတွင် Auto ဖျက်မည်
    schedule_delete(context, update.effective_chat.id, warn.message_id, 15)