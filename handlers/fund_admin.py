import re
import logging
from datetime import datetime, timezone, timedelta
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from database.db import get_db

# 🛡️ Centralized Auth ကို ခေါ်ယူအသုံးပြုခြင်း
from utils.auth import is_super_admin

logger = logging.getLogger(__name__)

# ================= 💰 ရန်ပုံငွေ အသစ်ဖန်တီးရန် (/newfund) =================
async def newfund_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """အသုံးပြုနည်း: /newfund <FundID> <TargetAmount> <Manager> [Fund Name]"""
    if not is_super_admin(update.effective_user.id): return #[cite: 1]
    
    args = context.args #[cite: 1]
    if len(args) < 3: 
        return await update.message.reply_text("⚠️ <b>အသုံးပြုနည်း:</b> <code>/newfund &lt;FundID&gt; &lt;TargetAmount&gt; &lt;Manager&gt; [Fund Name]</code>\n(ဥပမာ - <code>/newfund july 5000 ec လစဉ်ကြေး</code> သို့မဟုတ် <code>/newfund welcome 10000 ben Welcome Party</code>)", parse_mode=ParseMode.HTML)
    
    fund_id = args[0].lower() #[cite: 1]
    try:
        target = int(args[1]) #[cite: 1]
    except ValueError:
        return await update.message.reply_text("❌ ငွေပမာဏ (TargetAmount) ကို ဂဏန်းဖြင့်သာ ထည့်ပါ။") #[cite: 1]
        
    manager = args[2].lower() # 'ec' သို့မဟုတ် ကောက်ခံမည့်သူ၏ username
    fund_name = " ".join(args[3:]) if len(args) > 3 else fund_id #[cite: 1]

    db = get_db() #[cite: 1]
    # status: active နှင့် managed_by ကိုပါ DB တွင် သိမ်းဆည်းမည်
    await db.funds.update_one( #[cite: 1]
        {"fund_id": fund_id}, #[cite: 1]
        {"$set": {"target_amount": target, "fund_name": fund_name, "managed_by": manager, "status": "active"}}, #[cite: 1]
        upsert=True #[cite: 1]
    )
    await update.message.reply_text(f"✅ <b>ရန်ပုံငွေအသစ် ဖန်တီး/ပြင်ဆင်ပြီးပါပြီ။</b>\n\n📌 ID: <code>{fund_id}</code>\n🎯 Target: {target} MMK\n👤 Manager: <b>{manager}</b>\n🏷️ အမည်: {fund_name}", parse_mode=ParseMode.HTML) #[cite: 1]

# ================= 🗑 ရန်ပုံငွေ ဖျက်ရန် (/delfund) =================
async def delfund_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """အသုံးပြုနည်း: /delfund <FundID>"""
    if not is_super_admin(update.effective_user.id): return
    
    if not context.args:
        return await update.message.reply_text("⚠️ <b>အသုံးပြုနည်း:</b> <code>/delfund &lt;FundID&gt;</code>", parse_mode=ParseMode.HTML)
        
    fund_id = context.args[0].lower()
    db = get_db()
    res = await db.funds.delete_one({"fund_id": fund_id})
    
    if res.deleted_count:
        await update.message.reply_text(f"🗑️ ရန်ပုံငွေ <code>{fund_id}</code> ကို ဖျက်လိုက်ပါပြီ。", parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(f"⚠️ <code>{fund_id}</code> အမည်ဖြင့် ရန်ပုံငွေ မရှိပါ။", parse_mode=ParseMode.HTML)

# ================= 💰 ငွေသွင်းစာရင်း မှတ်ရန် (/pay) =================
async def payfund_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """အသုံးပြုနည်း: /pay <RollNo_Last_Digits> <FundID> <Amount>"""
    if not is_super_admin(update.effective_user.id): return
    
    args = context.args
    if len(args) < 3:
        return await update.message.reply_text(
            "⚠️ <b>အသုံးပြုနည်း မှားယွင်းနေပါသည်။</b>\n<pre>/pay &lt;RollNo&gt; &lt;FundID&gt; &lt;Amount&gt;</pre>\n(ဥပမာ - <code>/pay 01 sports 2000</code>)", 
            parse_mode=ParseMode.HTML
        )
    
    try:
        roll_query = args[0]
        fund_id = args[1].lower()
        amount = int(args[2])
        
        # 📌 Negative Amount ကာကွယ်ခြင်း (Bug Fix)
        if amount <= 0:
            return await update.message.reply_text("❌ <b>ငွေပမာဏ (Amount) သည် သုညထက် ကြီးရပါမည်။</b>", parse_mode=ParseMode.HTML)
        
        db = get_db()
        regex_pattern = re.compile(re.escape(roll_query) + "$", re.IGNORECASE)
        student = await db.student_info.find_one({"roll_no": regex_pattern})
        
        if not student:
            return await update.message.reply_text(f"⚠️ <code>{roll_query}</code> ဖြင့်ဆုံးသော ကျောင်းသားကို ရှာမတွေ့ပါ။", parse_mode=ParseMode.HTML)
            
        current_paid = student.get("paid_funds", {}).get(fund_id, 0)
        new_paid = current_paid + amount
        
        # 📌 ငွေသွင်းပေးလိုက်သော Admin ၏ အချက်အလက်ကို ယူခြင်း
        admin_name = update.effective_user.first_name
        admin_id = update.effective_user.id
        admin_info = f"{admin_name} ({admin_id})"

        mm_tz = timezone(timedelta(hours=6, minutes=30))
        current_date = datetime.now(mm_tz).strftime("%d-%b-%Y %I:%M %p")
        
        await db.student_info.update_one(
            {"_id": student["_id"]},
            {
                "$set": {
                    f"paid_funds.{fund_id}": new_paid,
                    "last_payment_date": current_date
                },
                "$push": {
                    "payment_history": {
                        "fund_id": fund_id,
                        "amount": amount,
                        "date": current_date,
                        "received_by": admin_info # 👈 Admin ဘယ်သူလဲဆိုတာပါ မှတ်သားထားမည်
                    }
                }
            }
        )
        
        await update.message.reply_text(
            f"✅ <b>စာရင်းသွင်းပြီးပါပြီ။</b>\n\n"
            f"👤 ကျောင်းသား: {student.get('name')} ({student.get('roll_no')})\n"
            f"📥 ယခုသွင်းငွေ: {amount} MMK\n"
            f"💰 စုစုပေါင်း သွင်းထားငွေ: {new_paid} MMK\n"
            f"🕒 အချိန်: <code>{current_date}</code>", 
            parse_mode=ParseMode.HTML
        )
    except ValueError:
        await update.message.reply_text("❌ ငွေပမာဏကို ဂဏန်းဖြင့်သာ ထည့်ပါ။")

# ================= ⏪ မှားထည့်မိသည်ကို ပြန်ရုပ်သိမ်းရန် (/undo) =================
async def undo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """အသုံးပြုနည်း: /undo <RollNo_Last_Digits> <FundID>"""
    if not is_super_admin(update.effective_user.id): return

    args = context.args
    if len(args) < 2:
        return await update.message.reply_text(
            "⚠️ <b>အသုံးပြုနည်း မှားယွင်းနေပါသည်။</b>\n<pre>/undo &lt;RollNo&gt; &lt;FundID&gt;</pre>\n(ဥပမာ - <code>/undo 01 sports</code>)", 
            parse_mode=ParseMode.HTML
        )

    roll_query = args[0]
    fund_id = args[1].lower()

    db = get_db()
    regex_pattern = re.compile(re.escape(roll_query) + "$", re.IGNORECASE)
    student = await db.student_info.find_one({"roll_no": regex_pattern})

    if not student:
        return await update.message.reply_text(f"⚠️ <code>{roll_query}</code> ဖြင့်ဆုံးသော ကျောင်းသားကို ရှာမတွေ့ပါ။", parse_mode=ParseMode.HTML)

    history = student.get("payment_history", [])
    fund_history = [h for h in history if h.get("fund_id") == fund_id]

    if not fund_history:
        return await update.message.reply_text(f"⚠️ <b>{fund_id}</b> အတွက် ပြန်ရုပ်သိမ်းစရာ ငွေသွင်းမှတ်တမ်း မရှိပါ။", parse_mode=ParseMode.HTML)

    last_tx = fund_history[-1]
    revert_amount = last_tx["amount"]
    tx_date = last_tx["date"]

    current_paid = student.get("paid_funds", {}).get(fund_id, 0)
    new_paid = max(0, current_paid - revert_amount)

    # 📌 Backward Iteration ဖြင့် ပိုမို Safe ဖြစ်စွာ List ထဲမှ ဖယ်ရှားခြင်း (Bug Fix)
    for i in range(len(history) - 1, -1, -1):
        item = history[i]
        if item.get("fund_id") == fund_id and item.get("amount") == revert_amount:
            history.pop(i)
            break

    await db.student_info.update_one(
        {"_id": student["_id"]},
        {
            "$set": {
                f"paid_funds.{fund_id}": new_paid,
                "payment_history": history
            }
        }
    )

    await update.message.reply_text(
        f"⏪ <b>လုပ်ဆောင်ချက် ပြန်လည်ရုပ်သိမ်းပြီးပါပြီ (Undo Successful)</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"👤 ကျောင်းသား: {student.get('name')} ({student.get('roll_no')})\n"
        f"🗑 ဖျက်လိုက်သော သွင်းငွေ: <b>{revert_amount} MMK</b> <i>(သွင်းခဲ့သည့်အချိန်: {tx_date})</i>\n"
        f"💰 ယခု ကျန်ရှိငွေ: <b>{new_paid} MMK</b>",
        parse_mode=ParseMode.HTML
    )

# ================= 📱 Phone & TG Username (Admin Manual Set) =================
async def setphone_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """အသုံးပြုနည်း: /setphone <RollNo> <Phone>"""
    if not is_super_admin(update.effective_user.id): return
    
    if len(context.args) < 2: return await update.message.reply_text("⚠️ <b>အသုံးပြုနည်း:</b> <code>/setphone &lt;RollNo&gt; &lt;Phone&gt;</code>", parse_mode=ParseMode.HTML)
    
    roll_query = context.args[0]
    phone = context.args[1]
    db = get_db()
    regex_pattern = re.compile(re.escape(roll_query) + "$", re.IGNORECASE)
    res = await db.student_info.update_one({"roll_no": regex_pattern}, {"$set": {"phone": phone}})
    
    if res.modified_count:
        await update.message.reply_text("✅ ဖုန်းနံပါတ် ထည့်သွင်း/ပြင်ဆင်ပြီးပါပြီ。")
    else:
        await update.message.reply_text("❌ ကျောင်းသား ရှာမတွေ့ပါ။")

async def settg_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """အသုံးပြုနည်း: /settg <RollNo> <Username>"""
    if not is_super_admin(update.effective_user.id): return
    
    if len(context.args) < 2: return await update.message.reply_text("⚠️ <b>အသုံးပြုနည်း:</b> <code>/settg &lt;RollNo&gt; &lt;Username&gt;</code>", parse_mode=ParseMode.HTML)
    
    roll_query = context.args[0]
    username = context.args[1].replace("@", "")
    db = get_db()
    regex_pattern = re.compile(re.escape(roll_query) + "$", re.IGNORECASE)
    res = await db.student_info.update_one({"roll_no": regex_pattern}, {"$set": {"tg_username": username}})
    
    if res.modified_count:
        await update.message.reply_text("✅ Telegram Username ထည့်သွင်း/ပြင်ဆင်ပြီးပါပြီ。")
    else:
        await update.message.reply_text("❌ ကျောင်းသား ရှာမတွေ့ပါ။")

# ================= 🔍 ငွေသွင်းမှတ်တမ်း အသေးစိတ်ကြည့်ရန် (/history) =================
async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """အသုံးပြုနည်း: /history <RollNo>"""
    if not is_super_admin(update.effective_user.id): return
    
    if not context.args:
        return await update.message.reply_text("⚠️ <b>အသုံးပြုနည်း:</b> <code>/history &lt;RollNo&gt;</code>\n(ဥပမာ - <code>/history 01</code>)", parse_mode=ParseMode.HTML)
    
    roll_query = context.args[0]
    db = get_db()
    regex_pattern = re.compile(re.escape(roll_query) + "$", re.IGNORECASE)
    student = await db.student_info.find_one({"roll_no": regex_pattern})
    
    if not student:
        return await update.message.reply_text(f"⚠️ <code>{roll_query}</code> ဖြင့်ဆုံးသော ကျောင်းသားကို ရှာမတွေ့ပါ။", parse_mode=ParseMode.HTML)
    
    history = student.get("payment_history", [])
    if not history:
        return await update.message.reply_text(f"⚠️ <b>{student.get('name')}</b> တွင် ငွေသွင်းမှတ်တမ်း မရှိသေးပါ။", parse_mode=ParseMode.HTML)
    
    msg = f"📜 <b>Payment History for {student.get('name')}</b>\n━━━━━━━━━━━━━━━━━━\n"
    
    # 📌 မှတ်တမ်းများကို တစ်ခုချင်းစီ ထုတ်ပြခြင်း
    for idx, h in enumerate(history, 1):
        msg += f"{idx}. <b>{h.get('fund_id').upper()}</b> - {h.get('amount')} MMK\n"
        msg += f"   📅 Date: {h.get('date')}\n"
        msg += f"   👤 Received By: <code>{h.get('received_by', 'Unknown Admin')}</code>\n\n"
        
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

