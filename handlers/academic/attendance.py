import os
import random
import logging
from datetime import datetime, timedelta, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from telegram.constants import ParseMode
from database.db import get_db

logger = logging.getLogger(__name__)

OWNER_ID = int(os.getenv("OWNER_ID", "0"))
ROLL_PIN_INPUT = 1

# ================= HELPER FUNCTIONS =================
async def is_ec_check(user_id: int) -> bool:
    """လက်ရှိ Approvers စာရင်းဝင် Admin များနှင့် Owner ကို EC ဟု သတ်မှတ်ပါသည်"""
    if user_id == OWNER_ID: return True
    db = get_db()
    if db is None: return False
    approver = await db.approvers.find_one({"user_id": user_id})
    return True if approver else False

def get_short_roll(full_roll: str) -> str:
    """Sem-II-CEIT-1 မှ ၁ လုံးတည်းဖြစ်အောင် နောက်ဆုံးဂဏန်း ဖြတ်ထုတ်ခြင်း"""
    try:
        if "-" in full_roll:
            return full_roll.split("-")[-1].strip()
        return full_roll
    except:
        return full_roll

# ================= 🎛️ EC: START ATTENDANCE (/attendance) =================
async def start_attendance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await is_ec_check(user_id):
        return await update.message.reply_text("⚠️ EC များနှင့် Admin များသာ သုံးခွင့်ရှိပါသည်။")
        
    db = get_db()
    if db is None: return
    
    # 6-Digit Random PIN ထုတ်ခြင်း
    pin_code = str(random.randint(100000, 999999))
    
    mm_tz = timezone(timedelta(hours=6, minutes=30))
    today_str = datetime.now(mm_tz).strftime("%Y-%m-%d")
    
    # လက်ရှိ ဖွင့်ထားဆဲ PIN ရှိပါက အရင် Closed လုပ်မည်
    await db.attendance_records.update_many({"status": "active"}, {"$set": {"status": "closed"}})
    
    # Record အသစ်ဆောက်ခြင်း
    record = {
        "date": today_str,
        "pin_code": pin_code,
        "status": "active",
        "created_by": user_id,
        "present_users": []
    }
    await db.attendance_records.insert_one(record)
    
    kb = [[InlineKeyboardButton("🛑 CLOSE ATTENDANCE (KILL)", callback_data=f"att_close_{pin_code}")]]
    await update.message.reply_text(
        f"🎲 <b>ROLL CALL STARTED</b>\n\n🔢 Secret PIN: <code>{pin_code}</code>\n📅 Date: <b>{today_str}</b>\n\n<i>ကျောင်းသားများအား Bot တွင် /rollcall နှိပ်၍ ဤ PIN ရိုက်ထည့်ရန် အော်ပေးလိုက်ပါဗျာ။</i>",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML
    )

async def close_attendance_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query.data.startswith("att_close_"): return
    await query.answer()
    
    if not await is_ec_check(update.effective_user.id): return
    
    pin = query.data.replace("att_close_", "")
    db = get_db()
    if db is not None:
        await db.attendance_records.update_one({"pin_code": pin}, {"$set": {"status": "closed"}})
        
    await query.edit_message_text("🛑 <b>ATTENDANCE CLOSED</b>\n\nPIN Code ကို ပိတ်သိမ်းလိုက်ပါပြီ။ စာရင်းသွင်း၍ မရတော့ပါ။", parse_mode=ParseMode.HTML)

# ================= 🎓 STUDENTS & EC: ROLL CALL SUBMISSION (/rollcall) =================
async def rollcall_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db = get_db()
    if db is None: return ConversationHandler.END
    
    # Database ထဲမှာ ကျောင်းသားစာရင်း ရှိ/မရှိ စစ်ဆေးခြင်း
    student = await db.students.find_one({"user_id": user_id})
    if not student:
        await update.message.reply_text("❌ <b>ခွင့်ပြုချက်မရှိပါ</b>\n\nသင့်အား စာရင်းသွင်းထားခြင်း မရှိပါ။ Admin ထံ ဆက်သွယ်ပါ။", parse_mode=ParseMode.HTML)
        return ConversationHandler.END
        
    await update.message.reply_text("🔢 <b>ROLL CALL ATTENDANCE</b>\n\nအတန်းထဲရှိ ကျောက်သင်ပုန်းမှ <b>6-Digit PIN Code</b> ကို ရိုက်ထည့်ပေးပါဗျာ:", parse_mode=ParseMode.HTML)
    return ROLL_PIN_INPUT

async def rollcall_receive_pin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pin_input = update.message.text.strip()
    user_id = update.effective_user.id
    db = get_db()
    if db is None: return ConversationHandler.END
    
    # Active ဖြစ်နေသော PIN နှင့် ကိုက်ညီမှု ရှိ/မရှိ စစ်ခြင်း
    record = await db.attendance_records.find_one({"pin_code": pin_input, "status": "active"})
    if not record:
        await update.message.reply_text("❌ PIN ကုဒ်မှားယွင်းနေပါသည်။ သို့မဟုတ် စာရင်းပိတ်သွားပါပြီ။ /rollcall ဖြင့် ပြန်စမ်းပါ။")
        return ConversationHandler.END
        
    # ထပ်ပြီး စာရင်းသွင်းခြင်း ရှိ/မရှိ စစ်ဆေးခြင်း
    present_list = record.get("present_users", [])
    already_present = any(u["user_id"] == user_id for u in present_list)
    if already_present:
        await update.message.reply_text("⚠️ သင်သည် ဤအတန်းအတွက် စာရင်းသွင်းပြီးသား ဖြစ်ပါသည်ဗျာ။")
        return ConversationHandler.END
        
    # ကျောင်းသား အချက်အလက် ယူခြင်း
    student = await db.students.find_one({"user_id": user_id})
    mm_tz = timezone(timedelta(hours=6, minutes=30))
    now_time = datetime.now(mm_tz).strftime("%I:%M %p")
    
    user_data = {
        "user_id": user_id,
        "roll_no": student["roll_no"],
        "name": student["name"],
        "time": now_time
    }
    
    # DB ထဲသို့ တက်ရောက်သူစာရင်း တိုးထည့်ခြင်း
    await db.attendance_records.update_one({"pin_code": pin_input}, {"$push": {"present_users": user_data}})
    
    short_roll = get_short_roll(student["roll_no"])
    await update.message.reply_text(
        f"✅ <b>ROLL CALL အောင်မြင်ပါသည်</b>\n━━━━━━━━━━━━━━━━━\n🆔 Roll No: <b>{short_roll}</b>\n👤 Name: <b>{student['name']}</b>\n🕒 Time: <code>{now_time}</code>\n📅 Date: <code>{record['date']}</code>",
        parse_mode=ParseMode.HTML
    )
    return ConversationHandler.END

async def rollcall_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚫 Cancelled.")
    return ConversationHandler.END

# ================= 📅 EC: VIEW HISTORY & PAGINATION (/dates) =================
async def view_dates_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_ec_check(update.effective_user.id): return
    await show_dates_page(update, context, page=0)

async def show_dates_page(update_or_query, context: ContextTypes.DEFAULT_TYPE, page: int):
    db = get_db()
    if db is None: return
    
    # နေ့စွဲများကို နောက်ဆုံးကောက်ထားသည့်ရက်မှစ၍ စီပြခြင်း (Latest First)
    cursor = db.attendance_records.find({}).sort("_id", -1)
    all_records = await cursor.to_list(length=None)
    
    if not all_records:
        msg_text = "📅 <b>ATTENDANCE HISTORY</b>\n━━━━━━━━━━━━━━━━━━\n🎉 ကောက်ထားသည့် စာရင်းများ မရှိသေးပါဗျာ။"
        if isinstance(update_or_query, Update): await update_or_query.message.reply_text(msg_text, parse_mode=ParseMode.HTML)
        else: await update_or_query.edit_message_text(msg_text, parse_mode=ParseMode.HTML)
        return

    per_page = 10
    total_pages = (len(all_records) + per_page - 1) // per_page
    start = page * per_page
    end = start + per_page
    page_records = all_records[start:end]
    
    kb = []
    for r in page_records:
        date_str = r["date"]
        pin = r["pin_code"]
        count = len(r.get("present_users", []))
        kb.append([InlineKeyboardButton(f"📅 {date_str} (PIN: {pin}) -> Total: {count}", callback_data=f"vdate_show_{pin}_{page}")])
        
    # Navigation Buttons
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"vdate_p_{page-1}"))
    if page < total_pages - 1: nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"vdate_p_{page+1}"))
    if nav: kb.append(nav)
    
    kb.append([InlineKeyboardButton("❌ Close", callback_data="vdate_close")])
    
    msg_text = f"📅 <b>ATTENDANCE HISTORY (Page: {page+1}/{total_pages})</b>\n━━━━━━━━━━━━━━━━━━━━━━━\nစာရင်းကြည့်လိုသည့် ရက်စွဲအား နှိပ်ပါ -"
    
    if isinstance(update_or_query, Update):
        await update_or_query.message.reply_text(msg_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    else:
        await update_or_query.edit_message_text(msg_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)

async def dates_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()
    
    if data == "vdate_close":
        return await query.edit_message_text("✅ Closed History.")
        
    if data.startswith("vdate_p_"):
        page = int(data.split("_")[2])
        return await show_dates_page(query, context, page)
        
    db = get_db()
    if db is None: return
    
    if data.startswith("vdate_show_") or data.startswith("vdate_abs_"):
        # Format: vdate_[show|abs]_[pin]_[page]
        mode, _, pin, page_str = data.split("_", 3)
        page = int(page_str)
        
        record = await db.attendance_records.find_one({"pin_code": pin})
        if not record: return
        
        present_list = record.get("present_users", [])
        
        if mode == "show":
            # 🟢 PRESENT LIST ပြသခြင်း
            txt = f"📅 <b>ATTENDANCE REPORT: {record['date']}</b>\n"
            txt += f"🔢 PIN Code: <code>{pin}</code>\n"
            txt += f"🟢 တက်ရောက်သူ (Present) စာရင်း:\n━━━━━━━━━━━━━━━━━━\n"
            
            # Short Roll No အလိုက် စီခြင်း
            present_list.sort(key=lambda x: int(get_short_roll(x['roll_no'])) if get_short_roll(x['roll_no']).isdigit() else 999)
            
            if not present_list:
                txt += "<i>(တက်ရောက်သူ မရှိပါ)</i>"
            for u in present_list:
                txt += f"✅ {get_short_roll(u['roll_no'])} - {u['name']} (<code>{u['time']}</code>)\n"
                
            kb = [[InlineKeyboardButton("❌ ပျက်ကွက်သူ (Absent) စာရင်းကြည့်ရန်", callback_data=f"vdate_abs_{pin}_{page}")]]
            kb.append([InlineKeyboardButton("🔙 Back to Dates", callback_data=f"vdate_p_{page}")])
            await query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
            
        elif mode == "abs":
            # 🔴 ABSENT LIST ပြသခြင်း
            all_students = await db.students.find({}).to_list(length=None)
            present_ids = [u["user_id"] for u in present_list]
            
            absent_list = [s for s in all_students if s["user_id"] not in present_ids]
            absent_list.sort(key=lambda x: int(get_short_roll(x['roll_no'])) if get_short_roll(x['roll_no']).isdigit() else 999)
            
            txt = f"📅 <b>ATTENDANCE REPORT: {record['date']}</b>\n"
            txt += f"🔢 PIN Code: <code>{pin}</code>\n"
            txt += f"🔴 ပျက်ကွက်သူ (Absent) စာရင်း:\n━━━━━━━━━━━━━━━━━━\n"
            
            if not absent_list:
                txt += "🎉 <i>ကျောင်းသားအားလုံး တက်ရောက်ကြသည်။ (All Present!)</i>"
            for s in absent_list:
                txt += f"❌ {get_short_roll(s['roll_no'])} - {s['name']}\n"
                
            kb = [[InlineKeyboardButton("🟢 တက်ရောက်သူ (Present) စာရင်းကြည့်ရန်", callback_data=f"vdate_show_{pin}_{page}")]]
            kb.append([InlineKeyboardButton("🔙 Back to Dates", callback_data=f"vdate_p_{page}")])
            await query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)