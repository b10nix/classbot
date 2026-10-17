import logging
import html
from datetime import datetime
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from database.db import get_db
from utils.auth import is_super_admin
from utils.constants import EC_MAP
from utils.helpers import schedule_delete, get_mm_time

logger = logging.getLogger(__name__)

async def end_semester_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Super Admin Command:
    Format: /endsemester <semester_name> <starting_balance> <ec_name>
    Example: /endsemester sem1_2026 23000 jojo
    
    1. Timetable အဟောင်းများကို Database မှ အပြီးတိုင် ဖျက်ပစ်မည်။
    2. Student များ၏ paid_funds နှင့် EC များ၏ ec_ledgers စာရင်းများကို Archive လုပ်ပြီး Clear မည်။
    3. သတ်မှတ်ထားသော EC ထံသို့ Starting Balance စာရင်းအသစ် ထည့်သွင်းပေးမည်။
    """
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    # 1. Super Admin Authorization Check
    if not is_super_admin(user_id):
        warn = await update.message.reply_text("⛔ ဤ Command ကို Super Admin သာ အသုံးပြုခွင့်ရှိပါသည်။")
        schedule_delete(context, chat_id, warn.message_id, 5)
        return

    # 2. Argument Validation
    if len(context.args) < 3:
        help_text = (
            "⚠️ <b>အသုံးပြုပုံ မမှန်ကန်ပါ။</b>\n\n"
            "Format: <code>/endsemester &lt;semester_name&gt; &lt;starting_balance&gt; &lt;ec_name&gt;</code>\n"
            "ဥပမာ: <code>/endsemester sem1_2026 23000 jojo</code>"
        )
        await update.message.reply_text(help_text, parse_mode=ParseMode.HTML)
        return

    semester_name = context.args[0].strip()

    try:
        starting_balance = float(context.args[1].strip())
    except ValueError:
        await update.message.reply_text("❌ Starting Balance သည် ကိန်းဂဏန်း (Number) သာ ဖြစ်ရပါမည်။")
        return

    ec_key = context.args[2].strip().lower().lstrip("@")

    # 3. Resolve EC via EC_MAP
    ec_user_id = None
    ec_display_name = context.args[2].strip()

    for key, val in EC_MAP.items():
        if key.lower() == ec_key:
            if isinstance(val, dict):
                ec_user_id = val.get("id") or val.get("user_id")
                ec_display_name = val.get("name", key)
            else:
                ec_user_id = val
                ec_display_name = key
            break

    db = get_db()
    if db is None:
        await update.message.reply_text("❌ Database ချိတ်ဆက်မှု မအောင်မြင်ပါ။")
        return

    # Fallback to database lookup if not present in EC_MAP
    if not ec_user_id:
        target_ec = await db.approvers.find_one({
            "$or": [
                {"username": {"$regex": f"^{ec_key}$", "$options": "i"}},
                {"name": {"$regex": f"^{ec_key}$", "$options": "i"}},
                {"nickname": {"$regex": f"^{ec_key}$", "$options": "i"}}
            ]
        })

        if target_ec:
            ec_user_id = target_ec.get("user_id") or target_ec.get("tg_id")
            ec_display_name = target_ec.get("name") or target_ec.get("nickname") or ec_key

    if not ec_user_id:
        await update.message.reply_text(
            f"❌ <b>'{html.escape(context.args[2].strip())}'</b> အမည်ရှိသော EC ကို ရှာမတွေ့ပါ။",
            parse_mode=ParseMode.HTML
        )
        return

    status_msg = await update.message.reply_text(
        "⏳ <i>Semester အဟောင်း Data များကို Archive လုပ်၍ Dashboard စာရင်းများ ရှင်းလင်းနေပါသည်...</i>", 
        parse_mode=ParseMode.HTML
    )

    try:
        now_str = get_mm_time()

        # Helper function: Collection များအား Archive သို့ Backup ကူးပြီး Clear ခြင်း
        async def archive_and_clear(col_name: str, archive_suffix: str):
            records = await db[col_name].find({}).to_list(length=None)
            if records:
                for r in records:
                    r["archived_date"] = now_str
                    r["archived_semester"] = semester_name
                await db[f"{archive_suffix}_{semester_name}"].insert_many(records)
                await db[col_name].delete_many({})
            return len(records)

        # Step A: Timetables များကို အပြီးတိုင် ဖျက်ပစ်ခြင်း
        timetable_del = await db.timetable.delete_many({})

        # Step B: Fund အဟောင်းများနှင့် Transactions များကို Archive Collection ထဲ ပြောင်းသိမ်းခြင်း
        fund_tx_count = await archive_and_clear("fund_transactions", "archive_fund_transactions")
        funds_count = await archive_and_clear("funds", "archive_funds")
        transfers_count = await archive_and_clear("transfers", "archive_transfers")

        # Step C: EC Dashboard ထဲရှိ ကျောင်းသားများ၏ ပေးသွင်းငွေမှတ်တမ်း (paid_funds) များကို Backup လုပ်ပြီး Reset ချခြင်း
        students = await db.student_info.find({"paid_funds": {"$ne": {}}}).to_list(length=None)
        if students:
            # ကျောင်းသားများ၏ ပေးသွင်းငွေမှတ်တမ်းဟောင်းကို Archive ပြုလုပ်ခြင်း
            archive_students_col = db[f"archive_student_payments_{semester_name}"]
            await archive_students_col.insert_many(students)
            
            # Active Database ရှိ ကျောင်းသားစာရင်းများမှ paid_funds ကို ရှင်းထုတ်ခြင်း
            await db.student_info.update_many(
                {},
                {"$set": {"paid_funds": {}}}
            )

        # Step D: EC Ledgers (သုံးစွဲငွေ၊ လွှဲပြောင်းငွေမှတ်တမ်းများ) ကို Backup လုပ်ပြီး Reset ချခြင်း
        ledgers = await db.ec_ledgers.find({}).to_list(length=None)
        if ledgers:
            archive_ledgers_col = db[f"archive_ec_ledgers_{semester_name}"]
            await archive_ledgers_col.insert_many(ledgers)
            
            # Active Ledgers ကို 0 သို့ Reset ချခြင်း
            await db.ec_ledgers.update_many(
                {},
                {"$set": {
                    "total_spent": 0,
                    "transferred_in": 0,
                    "transferred_out": 0,
                    "expenses": [],
                    "expense_history": []
                }}
            )

        # Step E: သတ်မှတ်ထားသော EC ထံသို့ Opening Balance အသစ် ထည့်သွင်းပေးခြင်း
        if starting_balance > 0:
            # 1. ဗဟို Fund Transaction ထဲသို့ သွင်းခြင်း
            await db.fund_transactions.insert_one({
                "type": "opening_balance",
                "amount": starting_balance,
                "description": f"Remaining fund carry-over from {semester_name}",
                "ec_id": ec_user_id,
                "ec_name": ec_display_name,
                "created_by": user_id,
                "date": now_str,
                "is_opening": True
            })

            # 2. EC Dashboard (/me, /ecstat) တွင် Income အဖြစ် ပေါ်စေရန် funds collection ထဲသို့ fund_id အပြည့်အစုံဖြင့် ထည့်သွင်းခြင်း
            await db.funds.insert_one({
                "fund_id": f"opening_{semester_name.lower()}",
                "fund_name": f"Carry-over Balance ({semester_name})",
                "target_amount": int(starting_balance),
                "managed_by": ec_key,
                "status": "active",
                "type": "opening_balance",
                "created_at": now_str
            })

            # 3. EC Ledger ၏ transferred_in ထဲသို့ Opening Balance ထည့်ပေးခြင်း
            await db.ec_ledgers.update_one(
                {"$or": [{"ec_id": ec_user_id}, {"user_id": ec_user_id}, {"username": ec_key}]},
                {
                    "$set": {
                        "ec_id": ec_user_id,
                        "user_id": ec_user_id,
                        "username": ec_key,
                        "total_spent": 0,
                        "transferred_out": 0,
                        "transferred_in": starting_balance,
                        "expenses": []
                    }
                },
                upsert=True
            )

        summary_text = (
            f"🏁 <b>SEMESTER ENDED & FULL RESET COMPLETED</b>\n\n"
            f"📁 <b>Archive Target:</b> <code>{semester_name}</code>\n"
            f"🗑️ <b>Deleted Timetables:</b> {timetable_del.deleted_count} ခု\n"
            f"📦 <b>Archived Collections:</b>\n"
            f"  • Active Funds: {funds_count} ခု\n"
            f"  • Transactions: {fund_tx_count} ခု\n"
            f"  • Transfers History: {transfers_count} ခု\n"
            f"  • Student Paid Records: {len(students)} ဦး\n"
            f"  • EC Ledgers Reset: {len(ledgers)} ဦး\n\n"
            f"💳 <b>New Opening Balance:</b> {starting_balance:,.2f} MMK\n"
            f"👤 <b>Assigned EC:</b> {html.escape(str(ec_display_name))} (<code>{ec_user_id}</code>)\n\n"
            f"<i>ကျောင်းသားများ၏ လစဉ်ကြေးစာရင်းဟောင်းများနှင့် EC ထွက်ငွေများအားလုံး ရှင်းလင်းပြီးဖြစ်၍ "
            f"Dashboard တွင် စတင်လက်ကျန်အသစ်သာ ပေါ်နေမည် ဖြစ်ပါသည်။</i>"
        )
        await status_msg.edit_text(summary_text, parse_mode=ParseMode.HTML)

    except Exception as e:
        logger.error(f"Error in end_semester_cmd: {e}")
        await status_msg.edit_text(f"❌ Error ဖြစ်ပေါ်ခဲ့ပါသည်: <code>{e}</code>", parse_mode=ParseMode.HTML)