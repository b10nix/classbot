import asyncio
import os
import io
import csv
import re
import logging
import openpyxl
from openpyxl.styles import PatternFill, Font
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from database.db import get_db

# Log ထုတ်ရန် သတ်မှတ်ခြင်း
logger = logging.getLogger(__name__)

def is_super_admin(user_id: int) -> bool:
    owner_id = int(os.getenv("OWNER_ID", "0"))
    if user_id == owner_id: 
        return True
    sa_str = os.getenv("SUPER_ADMINS", "")
    if sa_str:
        try:
            return user_id in [int(x.strip()) for x in sa_str.split(",") if x.strip()]
        except ValueError: 
            pass
    return False

def extract_paid_amount(paid_data) -> int:
    """Helper to safely extract amount whether it is an int, float, or structured dict."""
    if isinstance(paid_data, dict):
        return int(paid_data.get("amount", 0))
    elif isinstance(paid_data, (int, float)):
        return int(paid_data)
    return 0

# ================= 📋 ကျောင်းသားစာရင်းကြည့်ရန် (/list) =================
async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_super_admin(update.effective_user.id): 
        return
    
    db = get_db()
    students = await db.student_info.find({}).to_list(length=None)
    if not students: 
        return await update.message.reply_text("စာရင်းမရှိသေးပါ။")

    registered_nums = {int(re.search(r'(\d+)$', s.get("roll_no", "")).group(1)) for s in students if re.search(r'(\d+)$', s.get("roll_no", ""))}
    max_roll = max(registered_nums) if registered_nums else 59
    if max_roll < 59: 
        max_roll = 59 

    missing_rolls = [i for i in range(1, max_roll + 1) if i not in registered_nums]
    missing_str = ", ".join([f"{i:02d}" for i in missing_rolls])

    msg = (
        "📊 <b>ကျောင်းသား စာရင်းသွင်းမှု အခြေအနေ</b>\n━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔹 <b>စာရင်းသွင်းပြီးသူ:</b> {len(students)} ယောက်\n"
        f"🔹 <b>စာရင်းသွင်းရန် ကျန်ရှိသူ:</b> {len(missing_rolls)} ယောက်\n\n"
        "⏳ <b>စာရင်းသွင်းရန် ကျန်သေးသည့် ခုံနံပါတ်များ:</b>\n"
        f"<code>{missing_str if missing_str else 'အားလုံးသွင်းပြီးပါပြီ 🎉'}</code>"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

# ================= 🏠 အဆောင်နေ ကျောင်းသားများ (/hostel) =================
async def hostel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_super_admin(update.effective_user.id): 
        return
    
    db = get_db()
    students = await db.student_info.find({"hostel": {"$in": ["Yes", "yes", True]}}).sort("roll_no", 1).to_list(length=None)
    if not students: 
        return await update.message.reply_text("အဆောင်နေ ကျောင်းသား မရှိသေးပါ။")
    
    msg = "🏠 <b>အဆောင်နေ ကျောင်းသားများ</b>\n━━━━━━━━━━━━━━\n"
    for s in students: 
        msg += f"🔹 {s.get('roll_no')} - {s.get('name')}\n"
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

# ================= 🏦 ရန်ပုံငွေ အခြေအနေချုပ် (/vault) =================
async def vault_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Displays the current class vault summary including:
    - Opening/Carry-over Balance
    - Active Collections (Student Payments)
    - Total EC Expenses
    - Net Vault Balance
    """
    db = get_db()
    if db is None:
        return await update.message.reply_text("❌ Database connection error.")

    # 1. Fetch Carry-over / Opening Balance
    # First priority: fund_transactions collection
    opening_tx = await db.fund_transactions.find_one({
        "$or": [
            {"type": "opening_balance"},
            {"is_opening": True}
        ]
    })

    carry_over_amount = 0.0
    semester_label = "Current"

    if opening_tx:
        carry_over_amount = float(opening_tx.get("amount", 0.0))
        desc = opening_tx.get("description", "")
        if "from " in desc:
            semester_label = desc.split("from ")[-1].strip()
    else:
        # Fallback priority: funds collection
        opening_fund = await db.funds.find_one({"type": "opening_balance"})
        if opening_fund:
            carry_over_amount = float(opening_fund.get("amount") or opening_fund.get("target_amount") or 0.0)
            semester_label = opening_fund.get("fund_id", "").replace("opening_", "")

    # 2. Fetch Active Funds (Exclude archived)
    all_funds = await db.funds.find({}).to_list(length=None)
    archived_fund_ids = {
        str(f.get("fund_id", "")).lower() 
        for f in all_funds 
        if f.get("status") == "archived"
    }

    # 3. Calculate Total Active Student Collections
    students = await db.student_info.find({}).to_list(length=None)
    total_collected = 0.0

    for st in students:
        paid_funds = st.get("paid_funds", {})
        for f_id, f_data in paid_funds.items():
            if str(f_id).lower() not in archived_fund_ids:
                amt = f_data.get("amount", 0) if isinstance(f_data, dict) else f_data
                if isinstance(amt, (int, float)):
                    total_collected += float(amt)

    # 4. Calculate Total EC Expenses
    ec_ledgers = await db.ec_ledgers.find({}).to_list(length=None)
    total_spent = 0.0
    for ec in ec_ledgers:
        spent = ec.get("total_spent", 0)
        if isinstance(spent, (int, float)):
            total_spent += float(spent)

    # 5. Compute Net Vault Balance
    # Net = Opening Balance + Collections - Expenses
    net_balance = (carry_over_amount + total_collected) - total_spent

    msg = (
        f"🏦 <b>VAULT (လက်ရှိ ရန်ပုံငွေ အခြေအနေ)</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"💰 <b>Carry-over Balance ({semester_label}):</b> {carry_over_amount:,.0f} MMK\n\n"
        f"📥 <b>စုစုပေါင်း ကောက်ခံရငွေ:</b> {total_collected:,.0f} MMK\n"
        f"📤 <b>EC များ သုံးစွဲပြီးငွေ:</b> {total_spent:,.0f} MMK\n"
        f"📊 <b>လက်ကျန်ငွေ (Net Balance):</b> {net_balance:,.0f} MMK"
    )

    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

# ================= 📊 ရန်ပုံငွေ အခြေအနေခွဲကြည့်ရန် (/report) =================
async def report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_super_admin(update.effective_user.id):
        return
        
    if not context.args:
        return await update.message.reply_text(
            "⚠️ <b>အသုံးပြုနည်း:</b> <code>/report &lt;FundID&gt;</code>", 
            parse_mode=ParseMode.HTML
        )
    
    fund_id = context.args[0].lower()
    db = get_db()
    
    fund = await db.funds.find_one({"fund_id": re.compile(f"^{fund_id}$", re.IGNORECASE)})
    if not fund:
        return await update.message.reply_text("⚠️ ထိုအမည်ဖြင့် ရန်ပုံငွေ မရှိပါ။")
    
    target = fund.get("target_amount", 0)
    students = await db.student_info.find({}).to_list(length=None)
    
    paid_list, partial_list, unpaid_list = [], [], []
    total = 0
    
    for s in students:
        paid_data = s.get("paid_funds", {}).get(fund_id, 0)
        paid_amount = extract_paid_amount(paid_data)
            
        roll = s.get("roll_no", "Unknown")
        total += paid_amount
        
        match = re.search(r'(\d+)$', roll)
        roll_num = int(match.group(1)) if match else 9999
        data = {"roll": roll, "roll_num": roll_num, "paid": paid_amount}
        
        if paid_amount >= target and target > 0:
            paid_list.append(data)
        elif paid_amount > 0:
            partial_list.append(data)
        else:
            unpaid_list.append(data)
            
    paid_list.sort(key=lambda x: x["roll_num"])
    partial_list.sort(key=lambda x: x["roll_num"])
    unpaid_list.sort(key=lambda x: x["roll_num"])
    
    p_str = ', '.join([x["roll"] for x in paid_list])
    pt_str = ', '.join([f"{x['roll']} ({x['paid']:,})" for x in partial_list])
    up_str = ', '.join([x["roll"] for x in unpaid_list])

    msg = f"📊 <b>{fund.get('fund_name', fund_id.capitalize())} Report</b>\n━━━━━━━━━━━━━━\n"
    msg += f"🟢 <b>Paid ({len(paid_list)}):</b> {p_str if p_str else '-'}\n\n"
    msg += f"🟡 <b>Partial ({len(partial_list)}):</b> {pt_str if pt_str else '-'}\n\n"
    msg += f"🔴 <b>Unpaid ({len(unpaid_list)}):</b> {up_str if up_str else '-'}\n\n"
    msg += f"💰 <b>Total Collected:</b> {total:,} MMK"
    
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

# ================= 🔔 ငွေမသွင်းရသေးသူများကို သတိပေးရန် (/remind) =================
async def remind_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """အသုံးပြုနည်း: /remind <FundID>"""
    if not is_super_admin(update.effective_user.id): 
        return
    
    if not context.args:
        return await update.message.reply_text(
            "⚠️ <b>အသုံးပြုနည်း:</b> <code>/remind &lt;FundID&gt;</code>\n(ဥပမာ - <code>/remind sports</code>)", 
            parse_mode=ParseMode.HTML
        )
        
    fund_id = context.args[0].lower()
    db = get_db()
    
    fund = await db.funds.find_one({"fund_id": re.compile(f"^{fund_id}$", re.IGNORECASE)})
    if not fund:
        return await update.message.reply_text(
            f"⚠️ <code>{fund_id}</code> အမည်ဖြင့် ရန်ပုံငွေ မရှိပါ။ အရင်ဆုံး /newfund ဖြင့် ဖန်တီးပါ။", 
            parse_mode=ParseMode.HTML
        )
        
    target_amount = fund.get("target_amount", 0)
    fund_name = fund.get("fund_name", fund_id)
    
    raw_students = await db.student_info.find({}).to_list(length=None)
    if not raw_students:
        return await update.message.reply_text("⚠️ Database တွင် ကျောင်းသားစာရင်း မရှိသေးပါ။", parse_mode=ParseMode.HTML)
        
    # Duplicate ကျောင်းသားစာရင်းများ ရှင်းလင်းခြင်း
    unique_students = {}
    for st in raw_students:
        r_no = st.get("roll_no")
        if not r_no: 
            continue
            
        existing = unique_students.get(r_no)
        has_id = bool(st.get("user_id") or st.get("tg_id"))
        existing_has_id = bool(existing.get("user_id") or existing.get("tg_id")) if existing else False
        
        if not existing or (not existing_has_id and has_id):
            unique_students[r_no] = st
            
    students = list(unique_students.values())
    
    await update.message.reply_text(f"⏳ <b>{fund_name}</b> အတွက် သတိပေးစာ စတင်ပို့ဆောင်နေပါသည်...", parse_mode=ParseMode.HTML)
    
    success_count = 0
    failed_students = []
    
    for student in students:
        paid_data = student.get("paid_funds", {}).get(fund_id, 0)
        paid = extract_paid_amount(paid_data)
        remaining = target_amount - paid
        
        if remaining > 0:
            user_id = student.get("user_id") or student.get("tg_id") 
            roll_no = student.get("roll_no", "Unknown")
            tg_uname = student.get("tg_username", "")
            
            if tg_uname:
                display_fail = f"{roll_no} (@{tg_uname})"
            else:
                display_fail = f"{roll_no} (No Username)"

            if not user_id:
                failed_students.append(display_fail)
                continue
                
            remind_text = (
                f"🔔 <b>FUND REMINDER (သတိပေးချက်)</b>\n━━━━━━━━━━━━━━━━━━\n"
                f"👤 အမည်: <b>{student.get('name')}</b>\n"
                f"🏷️ ရန်ပုံငွေ: <b>{fund_name}</b>\n\n"
                f"🎯 သတ်မှတ်ထားသော ပမာဏ: {target_amount:,} MMK\n"
                f"📥 သွင်းပြီးထားသော ပမာဏ: {paid:,} MMK\n"
                f"⚠️ <b>ထပ်မံသွင်းရန် လိုအပ်ငွေ: {remaining:,} MMK</b>\n━━━━━━━━━━━━━━━━━━\n"
                f"<i>ကျေးဇူးပြု၍ သတ်မှတ်ရက်အတွင်း EC များထံအမြန်ဆုံး ပေးသွင်းပေးပါရန် မေတ္တာရပ်ခံအပ်ပါသည်။</i>"
            )
            
            try:
                await context.bot.send_message(chat_id=user_id, text=remind_text, parse_mode=ParseMode.HTML)
                success_count += 1
                await asyncio.sleep(0.5) 
            except Exception as e:
                logger.error(f"Failed to remind user {user_id}: {e}")
                failed_students.append(display_fail) 
                
    failed_count = len(failed_students)
    summary_msg = (
        f"✅ <b>သတိပေးခြင်း ပြီးဆုံးပါပြီ။</b>\n\n"
        f"📩 အောင်မြင်စွာ ပို့ပြီးသူ: <b>{success_count}</b> ဦး\n"
        f"❌ ပို့၍မရသူ စုစုပေါင်း: <b>{failed_count}</b> ဦး"
    )
    
    if failed_count > 0:
        fail_list_str = "\n".join([f"• {fs}" for fs in failed_students])
        summary_msg += f"\n\n<b>[ ⚠️ ပို့၍မရသူများ စာရင်း ]</b>\n{fail_list_str}"
        
    await update.message.reply_text(summary_msg, parse_mode=ParseMode.HTML)

# ================= 📂 စာရင်းအပြည့်အစုံ CSV ထုတ်ရန် (/export) =================
async def export_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_super_admin(update.effective_user.id): 
        return
    
    db = get_db()
    students = await db.student_info.find({}).to_list(length=None)
    if not students: 
        return await update.message.reply_text("စာရင်းမရှိပါ။")
    
    funds = await db.funds.find({}).to_list(length=None)
    fund_ids = [f.get("fund_id") for f in funds if f.get("fund_id")]
    fund_names = [f.get("fund_name", f_id) for f, f_id in zip(funds, fund_ids)]
    
    students.sort(key=lambda x: int(re.search(r'(\d+)$', x.get("roll_no", "0")).group(1)) if re.search(r'(\d+)$', x.get("roll_no", "0")) else 9999)
    
    output = io.StringIO()
    writer = csv.writer(output)
    
    headers = ["Roll No", "Name"] + fund_names
    writer.writerow(headers)
    
    for s in students:
        row = [s.get("roll_no", ""), s.get("name", "")]
        for f_id in fund_ids:
            paid_data = s.get("paid_funds", {}).get(f_id, 0)
            row.append(extract_paid_amount(paid_data))
        writer.writerow(row)
        
    output.seek(0)
    await update.message.reply_document(
        document=output.getvalue().encode('utf-8-sig'), 
        filename="Class_Full_Report.csv", 
        caption="✅ <b>ကျောင်းသားစာရင်းနှင့် ရန်ပုံငွေများ အပြည့်အစုံ ထုတ်ပြီးပါပြီ။</b>\n<i>(Phone, Telegram, Hostel များ ဖြုတ်ထားပါသည်)</i>", 
        parse_mode=ParseMode.HTML
    )

# ================= 📂 သတ်မှတ် Fund ID အလိုက် Excel (.xlsx) ထုတ်ယူခြင်း (/exportfund) =================
async def export_fund_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_super_admin(update.effective_user.id): 
        return
    if len(context.args) < 1: 
        return await update.message.reply_text("⚠️ <b>အသုံးပြုနည်း:</b> <code>/exportfund &lt;FundID&gt;</code>", parse_mode=ParseMode.HTML)
        
    fund_id = context.args[0].lower()
    db = get_db()
    
    fund = await db.funds.find_one({"fund_id": re.compile(f"^{fund_id}$", re.IGNORECASE)})
    if not fund: 
        return await update.message.reply_text(f"⚠️ <code>{fund_id}</code> အမည်ဖြင့် ရန်ပုံငွေ မရှိပါ။", parse_mode=ParseMode.HTML)
        
    fund_name = fund.get("fund_name", fund_id)
    students = await db.student_info.find({}).to_list(length=None)
    if not students: 
        return await update.message.reply_text("⚠️ Database ထဲတွင် ကျောင်းသား အချက်အလက် မရှိသေးပါ။")

    export_data, total_collected = [], 0
    for s in students:
        roll = s.get("roll_no", "➖")
        match = re.search(r'(\d+)$', roll)
        paid_data = s.get("paid_funds", {}).get(fund_id, 0)
        paid_amount = extract_paid_amount(paid_data)
        
        export_data.append({
            "roll": roll, 
            "roll_num": int(match.group(1)) if match else 9999, 
            "name": s.get("name", "➖"), 
            "status": "Paid" if paid_amount > 0 else "Unpaid", 
            "paid": paid_amount
        })
        total_collected += paid_amount
        
    export_data.sort(key=lambda x: (-x["paid"], x["roll_num"]))
    
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = fund_name[:31]
    
    ws.append(["Roll No", "Name", "Status", "Amount"])
    for col in range(1, 5): 
        ws.cell(row=1, column=col).font = Font(bold=True)
        
    green_fill, green_font = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid"), Font(color="006100")
    red_fill, red_font = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid"), Font(color="9C0006")
    
    for row_idx, d in enumerate(export_data, start=2):
        ws.cell(row=row_idx, column=1, value=d["roll"])
        ws.cell(row=row_idx, column=2, value=d["name"])
        status_cell = ws.cell(row=row_idx, column=3, value=d["status"])
        
        if d["status"] == "Paid": 
            status_cell.fill, status_cell.font = green_fill, green_font
        else: 
            status_cell.fill, status_cell.font = red_fill, red_font
            
        ws.cell(row=row_idx, column=4, value=d["paid"])
        
    last_row = len(export_data) + 3
    ws.cell(row=last_row, column=3, value="TOTAL:").font = Font(bold=True)
    ws.cell(row=last_row, column=4, value=total_collected).font = Font(bold=True)
    
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    
    await update.message.reply_document(
        document=output, 
        filename=f"{fund_name}_Report.xlsx", 
        caption=f"✅ <b>{fund_name} အတွက် စာရင်းထုတ်ပြီးပါပြီ။</b>\n\n🔹 စုစုပေါင်း ရငွေ: <b>{total_collected:,} MMK</b>", 
        parse_mode=ParseMode.HTML
    )