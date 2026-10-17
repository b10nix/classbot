import re
import uuid
import logging
from telegram import Update, InlineQueryResultArticle, InputTextMessageContent
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from database.db import get_db

# 🛡️ Centralized Auth ကို ခေါ်ယူအသုံးပြုခြင်း
from utils.auth import is_super_admin

logger = logging.getLogger(__name__)

# ================= 🛠️ Helper: UI တည်ဆောက်ခြင်း =================
def build_fund_text(student_paid: dict, funds: list) -> str:
    """ရန်ပုံငွေ အခြေအနေကို ပုံဖော်ပေးမည့် Helper Function"""
    if not funds: return "<i>ရန်ပုံငွေ မရှိသေးပါ</i>"
    
    text = ""
    for f in funds:
        f_id = f.get("fund_id")
        if not f_id: continue
        f_name = f.get("fund_name", f_id)
        target = f.get("target_amount", 0)
        
        # 📌 dictionary နှင့် int နှစ်မျိုးလုံးအတွက် အလုပ်လုပ်အောင် ပြင်ဆင်ထားသည်
        f_data = student_paid.get(f_id, 0)
        paid = f_data.get("amount", 0) if isinstance(f_data, dict) else f_data
        
        if paid >= target:
            text += f"✅ <b>{f_name}:</b> {paid:,} / {target:,}\n"
        elif paid > 0:
            remain = target - paid
            text += f"🟡 <b>{f_name}:</b> {paid:,} / {target:,} (⚠️ {remain:,} လိုသေး)\n"
        else:
            text += f"🔴 <b>{f_name}:</b> 0 / {target:,} (⚠️ {target:,} လိုသေး)\n"
    return text

def build_student_message(student: dict, funds: list) -> str:
    """ကျောင်းသားတစ်ယောက်ချင်းစီ၏ Message ကို ပုံဖော်ပေးမည့် Helper Function"""
    name = student.get("name", "Unknown")
    roll = student.get("roll_no", "Unknown")
    phone = student.get("phone", "No Phone")
    hostel = "Yes" if str(student.get("hostel", "")).lower() in ["yes", "true", "1"] else "No"
    
    tg_username = student.get("tg_username")
    # 📌 tg_id (သို့) user_id နှစ်မျိုးလုံးကို စစ်ဆေးယူမည်
    tg_id = student.get("user_id") or student.get("tg_id") 
    
    # 📌 [THE UPGRADE] Telegram ID ကို အဓိကထား၍ အသေချိတ်ဆက်မည်
    if tg_id:
        tg_link = f"<a href='tg://user?id={tg_id}'>👤 View Profile</a>"
    elif tg_username:
        tg_link = f"@{tg_username.replace('@', '')}"
    else:
        tg_link = "Not Linked"

    fund_text = build_fund_text(student.get("paid_funds", {}), funds)

    msg = (
        f"👤 <b>Name:</b> {name}\n"
        f"🎓 <b>Roll No:</b> {roll}\n"
        f"🏠 <b>Hostel:</b> {hostel}\n"
        f"📞 <b>Phone:</b> {phone}\n"
        f"💬 <b>Telegram:</b> {tg_link}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>Fund Status:</b>\n{fund_text}"
    )
    return msg

# ================= 🔍 Inline Search (Super Admin Only) =================
async def inline_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.inline_query.from_user.id
    
    # 📌 Admin မဟုတ်ပါက Friendly Access Denied Message ပြမည်
    if not is_super_admin(user_id):
        result = [
            InlineQueryResultArticle(
                id=str(uuid.uuid4()),
                title="⛔ Access Denied",
                description="Super Admin သာလျှင် Search အသုံးပြုခွင့်ရှိပါသည်။",
                input_message_content=InputTextMessageContent(
                    "⛔ <b>Access Denied:</b> You do not have Level 9 Clearance to view this record.", 
                    parse_mode=ParseMode.HTML
                )
            )
        ]
        return await update.inline_query.answer(result, cache_time=1, is_personal=True)

    query = update.inline_query.query.strip()
    if not query:
        return await update.inline_query.answer([], cache_time=1)

    db = get_db()
    
    # 📌 1. Database မှ Fund အားလုံးကို ဆွဲထုတ်မည်
    all_funds = await db.funds.find({
    "status": "active",
    "type": {"$ne": "opening_balance"}  # opening_balance မဟုတ်သော တကယ့် ကျောင်းသားကြေးများကိုသာ ဆွဲမည်
    }).to_list(length=None)
    
    # 📌 2. Archived ဖြစ်နေသော Fund များကို ရှာဖွေမည်
    sys_config = await db.sys_config.find_one({"type": "archived_funds"})
    archived_fund_ids = set()
    if sys_config:
        for f_id in sys_config.get("funds", []):
            archived_fund_ids.add(f_id.lower())

    # 📌 3. Active ဖြစ်သော Fund များကိုသာ စစ်ထုတ်ယူမည် (Archived ကို လုံးဝ ဖြတ်ထုတ်မည်)
    active_funds = []
    for f in all_funds:
        f_id = f.get("fund_id", "")
        if not f_id: continue
        
        clean_f_id = f_id.lower()
        if f.get("status") == "archived" or clean_f_id in archived_fund_ids:
            continue  # Archive ထဲပါနေလျှင် ကျော်သွားမည်
            
        active_funds.append(f)
    
    # 📌 4. ကျောင်းသားများကို ရှာဖွေမည်
    regex = re.compile(re.escape(query), re.IGNORECASE)
    students = await db.student_info.find({"$or": [{"roll_no": regex}, {"name": regex}]}).to_list(length=15)

    results = []
    for s in students:
        roll = s.get("roll_no", "Unknown")
        name = s.get("name", "Unknown")
        
        # 📌 Active Funds များကိုသာ Helper သို့ ပို့ပေးမည်ဖြစ်၍ Archive များ လုံးဝ ပေါ်လာမည်မဟုတ်ပါ
        msg_text = build_student_message(s, active_funds)
        
        results.append(
            InlineQueryResultArticle(
                id=str(uuid.uuid4()),
                title=f"{roll} - {name}",
                description=f"Phone: {s.get('phone', 'No Phone')}",
                input_message_content=InputTextMessageContent(msg_text, parse_mode=ParseMode.HTML)
            )
        )
        
    await update.inline_query.answer(results, cache_time=1)

# ================= 📌 Add Realistic Dummy Data =================
async def add_dummy_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_super_admin(update.effective_user.id): return
    
    db = get_db()
    
    # Dummy Fund ထည့်သွင်းခြင်း
    await db.funds.update_one(
        {"fund_id": "sports"}, 
        {"$set": {"target_amount": 5000, "fund_name": "Sports Fund", "status": "active"}}, 
        upsert=True
    )
    
    # Dummy Students များ ထည့်သွင်းခြင်း
    for i in range(1, 6):
        await db.student_info.insert_one({
            "roll_no": f"Sem-2-CEIT-{i:02d}",
            "name": f"Dummy User {i}",
            "phone": f"0912345678{i}",
            "hostel": "Yes" if i % 2 == 0 else "No",
            "tg_username": f"dummy_{i}",
            "paid_funds": {"sports": 2000 * i}
        })
        
    await update.message.reply_text("✅ Realistic Dummy Data များ (Funds, Students, Payments) ထည့်သွင်းပြီးပါပြီ။")