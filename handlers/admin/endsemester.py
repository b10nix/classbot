# handlers/admin/endsemester.py
"""Non-destructive semester close: records are retained for /semhistory."""

import logging
import html
from datetime import datetime, timezone, timedelta
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from database.db import get_db
from utils.auth import is_super_admin
from utils.helpers import get_mm_time
from services.major_service import get_user_major, resolve_ec_in_major
from services.finance_service import build_major_filter, build_fund_major_filter, get_major_semester_context
from services.audit_service import log_audit

logger = logging.getLogger(__name__)


def _mm_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=6, minutes=30)))


async def end_semester_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Close current funds without deleting students, payments, transactions or history.

    Usage is kept compatible: /endsemester <semester_name> <starting_balance> <ec_name>
    Follow with /setsemester <major> <new_semester> [academic_year] when the new term starts.
    """
    user_id = update.effective_user.id
    if not is_super_admin(user_id):
        return await update.message.reply_text("⛔ ဤ Command ကို Super Admin သာ အသုံးပြုခွင့်ရှိပါသည်။")

    major = await get_user_major(user_id)
    if not major:
        return await update.message.reply_text("❌ Active Major ကို မသတ်မှတ်နိုင်ပါ။ /setmajor <major> ဖြင့် အရင်ရွေးပါ။")
    if len(context.args) < 3:
        return await update.message.reply_text(
            "⚠️ အသုံးပြုပုံ: <code>/endsemester &lt;semester_name&gt; &lt;starting_balance&gt; &lt;ec_name&gt;</code>\n"
            "ဥပမာ: <code>/endsemester sem2_2026 23000 jojo</code>\n\n"
            "ဒီ command က old records ကို ဖျက်မည်မဟုတ်ပါ။ Fund တွေကို Completed လုပ်ပြီး /semhistory မှာ ပြန်ကြည့်နိုင်မယ်။\n"
            "Semester အသစ်ကို စတင်မယ်ဆိုရင် /setsemester ကို သီးခြားသုံးပါ။",
            parse_mode=ParseMode.HTML,
        )

    semester_name = context.args[0].strip()[:80]
    try:
        starting_balance = float(context.args[1].strip())
        if starting_balance < 0:
            raise ValueError
    except ValueError:
        return await update.message.reply_text("❌ Starting Balance သည် 0 သို့မဟုတ် အပေါင်းကိန်း ဖြစ်ရပါမည်။")

    ec_key = context.args[2].strip().lower().lstrip("@")
    ec_user_id, ec_display_name = await resolve_ec_in_major(ec_key, major)
    if not ec_user_id:
        return await update.message.reply_text(
            f"❌ <b>[{major.upper()}]</b> မေဂျာတွင် <b>'{html.escape(context.args[2])}'</b> အမည်ရှိသော EC မတွေ့ပါ။",
            parse_mode=ParseMode.HTML,
        )

    db = get_db(major=major)
    if db is None:
        return await update.message.reply_text("❌ Database ချိတ်ဆက်မှု မအောင်မြင်ပါ။")

    status_msg = await update.message.reply_text(
        f"⏳ <b>[{major.upper()}] Semester close ကို non-destructive mode ဖြင့် လုပ်ဆောင်နေပါသည်...</b>",
        parse_mode=ParseMode.HTML,
    )
    try:
        now_str = get_mm_time()
        current_semester, academic_year = await get_major_semester_context(major)
        m_filter = build_major_filter(major)
        archive_tag = f"{major}_{semester_name}".lower().replace(" ", "_")
        prior_close = await db.semester_archives.find_one({"major": major, "archive_tag": archive_tag, "archive_type": "semester_close"})
        if prior_close:
            return await status_msg.edit_text(
                f"⚠️ <b>{html.escape(archive_tag)}</b> အတွက် semester close လုပ်ပြီးသားဖြစ်ပါတယ်။\n"
                "ထပ်မံ run မလုပ်ပါနှင့်—opening balance နှစ်ခါမှတ်မိနိုင်ပါတယ်။ /semhistory မှ စစ်ဆေးပါ။",
                parse_mode=ParseMode.HTML,
            )

        active_funds = await db.funds.find({"$and": [build_fund_major_filter(major), {"type": {"$ne": "opening_balance"}, "$or": [{"status": "active"}, {"status": {"$exists": False}}]}]}).to_list(length=None)
        fund_ids = [str(f.get("fund_id")) for f in active_funds if f.get("fund_id")]
        # Mark records closed; do not move or delete original fund/payment documents.
        for fund in active_funds:
            await db.funds.update_one({"_id": fund["_id"]}, {"$set": {
                "status": "completed", "closed_at": now_str, "closed_by": user_id,
                "closed_semester": semester_name,
                "semester": fund.get("semester", current_semester),
                "academic_year": fund.get("academic_year", academic_year),
            }})

        ledgers = await db.ec_ledgers.find(m_filter).to_list(length=None)
        students = await db.student_info.find(m_filter).to_list(length=None)
        # Snapshot ledger details in a dedicated close record before starting fresh counters.
        collected_total = 0
        for student in students:
            for fund_id in fund_ids:
                paid = (student.get("paid_funds") or {}).get(fund_id, 0)
                if isinstance(paid, dict):
                    collected_total += int(paid.get("amount", 0) or 0)
                elif isinstance(paid, (int, float)):
                    collected_total += int(paid)

        archive_doc = {
            "archive_type": "semester_close", "archive_tag": archive_tag,
            "major": major, "semester_name": semester_name,
            "current_semester_before_close": current_semester,
            "academic_year": academic_year, "closed_at": now_str, "closed_by": user_id,
            "fund_ids": fund_ids, "fund_count": len(fund_ids),
            "student_count": len(students), "ledger_count": len(ledgers),
            "collected_from_closed_funds": collected_total,
            "starting_balance": int(starting_balance),
            "ledger_snapshots": [
                {"ec_id": row.get("ec_id") or row.get("user_id"),
                 "username": row.get("username"), "total_spent": row.get("total_spent", 0),
                 "transferred_in": row.get("transferred_in", 0),
                 "transferred_out": row.get("transferred_out", 0),
                 "expenses": row.get("expenses", []),
                 "expense_history": row.get("expense_history", [])}
                for row in ledgers
            ],
        }
        await db.semester_archives.insert_one(archive_doc)

        # Keep immutable old details in semester_archives. Reset only current-period counters.
        for ledger in ledgers:
            old_expenses = ledger.get("expenses", []) or []
            await db.ec_ledgers.update_one({"_id": ledger["_id"]}, {"$set": {
                "total_spent": 0, "transferred_in": 0, "transferred_out": 0,
                "expenses": [], "current_semester": current_semester,
                "last_semester_closed_at": now_str,
            }})

        if starting_balance > 0:
            await db.fund_transactions.insert_one({
                "major": major, "type": "opening_balance", "amount": int(starting_balance),
                "description": f"Carry-over balance after {semester_name}",
                "ec_id": ec_user_id, "ec_name": ec_display_name,
                "created_by": user_id, "date": now_str, "is_opening": True,
            })
            await db.ec_ledgers.update_one(
                {"$and": [m_filter, {"$or": [{"ec_id": ec_user_id}, {"user_id": ec_user_id}, {"username": ec_key}]}]},
                {"$set": {"major": major, "ec_id": ec_user_id, "user_id": ec_user_id,
                          "username": ec_key, "total_spent": 0, "transferred_out": 0,
                          "transferred_in": int(starting_balance),
                          "opening_note": f"Carry-over after {semester_name}", "expenses": [],
                          "current_semester": current_semester}},
                upsert=True,
            )

        await log_audit(db, major=major, actor_id=user_id, action="semester_closed",
                        entity_type="semester", entity_id=archive_tag, target_user_id=ec_user_id,
                        target_name=ec_display_name, details={"fund_count": len(fund_ids),
                        "collected": collected_total, "starting_balance": int(starting_balance)},
                        semester=current_semester)

        summary = (
            f"✅ <b>[{major.upper()}] Semester Close ပြီးပါပြီ</b>\n━━━━━━━━━━━━━━━━━━\n"
            f"📦 History record: <code>{html.escape(archive_tag)}</code>\n"
            f"💰 Completed funds: <b>{len(fund_ids)}</b> ခု\n"
            f"👥 ကျောင်းသား records: <b>{len(students)}</b> ဦး (မဖျက်ပါ)\n"
            f"📚 EC ledger snapshots: <b>{len(ledgers)}</b> ခု\n"
            f"📥 Close လုပ်ထားသော funds ရငွေ: <b>{collected_total:,} MMK</b>\n"
            f"💵 Opening/carry-over balance: <b>{starting_balance:,.0f} MMK</b>\n"
            f"👤 တာဝန်ခံ EC: <b>{html.escape(str(ec_display_name))}</b>\n\n"
            "Payment history နဲ့ student paid_funds ကို မဖျက်ထားပါ။ အဟောင်းတွေကို <code>/semhistory</code> မှ ကြည့်နိုင်ပါတယ်။\n"
            "Semester အသစ်စတင်တဲ့အခါ <code>/setsemester &lt;major&gt; &lt;semester&gt; [academic_year]</code> ကို ဆက်သုံးပါ။"
        )
        await status_msg.edit_text(summary, parse_mode=ParseMode.HTML)
    except Exception as exc:
        logger.exception("Non-destructive end semester failed for major=%s", major)
        await status_msg.edit_text(
            f"❌ Error ဖြစ်ပေါ်ခဲ့ပါသည်။ Data များကို မဖျက်ထားပါ။\n<code>{html.escape(str(exc))[:800]}</code>",
            parse_mode=ParseMode.HTML,
        )
