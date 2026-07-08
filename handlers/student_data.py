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
        paid = student_paid.get(f_id, 0)
        
        if paid >= target:
            text += f"✅ <b>{f_name}:</b> {paid} / {target}\n"
        elif paid > 0:
            remain = target - paid
            text += f"🟡 <b>{f_name}:</b> {paid} / {target} (⚠️ {remain} လိုသေး)\n"
        else:
            text += f"🔴 <b>{f_name}:</b> 0 / {target} (⚠️ {target} လိုသေး)\n"
    return text

def build_student_message(student: dict, funds: list) -> str:
    """ကျောင်းသားတစ်ယောက်ချင်းစီ၏ Message ကို ပုံဖော်ပေးမည့် Helper Function"""
    name = student.get("name", "Unknown")
    roll = student.get("roll_no", "Unknown")
    phone = student.get("phone", "No Phone")
    hostel = "Yes" if str(student.get("hostel", "")).lower() in ["yes", "true", "1"] else "No"
    
    tg_username = student.get("tg_username")
    tg_id = student.get("user_id")
    
    # Telegram Link ချိတ်ဆက်ခြင်း
    if tg_username:
        tg_link = f"@{tg_username}"
    elif tg_id:
        tg_link = f"<a href='tg://user?id={tg_id}'>Profile Link</a>"
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
    
    # 📌 DB ကို အကြိမ်ကြိမ်မခေါ်ဘဲ Fund Data ကို တစ်ခါတည်း Cache လုပ်ယူထားမည်
    funds = await db.funds.find({}).to_list(length=None)
    
    regex = re.compile(re.escape(query), re.IGNORECASE)
    students = await db.student_info.find({"$or": [{"roll_no": regex}, {"name": regex}]}).to_list(length=15)

    results = []
    for s in students:
        roll = s.get("roll_no", "Unknown")
        name = s.get("name", "Unknown")
        
        msg_text = build_student_message(s, funds)
        
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
        {"$set": {"target_amount": 5000, "fund_name": "Sports Fund"}}, 
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
            "paid_funds": {"sports": 2000 * i}  # ပေးသွင်းငွေ အတိုးအလျှော့လေးတွေ ပါဝင်မည်
        })
        
    await update.message.reply_text("✅ Realistic Dummy Data များ (Funds, Students, Payments) ထည့်သွင်းပြီးပါပြီ။")