"""Telegram handlers for /nuke's two-person permanent Fund deletion workflow."""
from __future__ import annotations

import html
import logging
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from database.db import get_db
from services.audit_service import log_audit
from services.finance_service import build_major_filter
from services.nuke_approval_service import (
    APPROVAL_TTL,
    COLLECTION_NAME,
    NukeApprovalError,
    NukeNoApprover,
    NukePermissionError,
    NukeRequestUnavailable,
    NukeSnapshotChanged,
    cancel_request,
    create_draft,
    execute_approved_cleanup,
    expire_request_if_due,
    get_request,
    is_active_ec,
    now_utc_naive,
    record_delivery,
    reject_request,
    revert_if_undelivered,
    submit_for_approval,
)
from services.major_service import get_user_major, normalize_major
from utils.constants import MAJOR_GROUP_IDS
from utils.nuke_keyboards import approver_keyboard, draft_keyboard, fund_picker_keyboard, pending_keyboard

logger = logging.getLogger(__name__)
PAGE_SIZE = 6


def _safe(value) -> str:
    return html.escape(str(value if value is not None else ""))


def _money(value) -> str:
    try:
        return f"{int(value or 0):,} MMK"
    except (TypeError, ValueError):
        return "0 MMK"


def _preview_text(request: dict, *, pending: bool = False) -> str:
    expires_at = request.get("expires_at")
    expiry_text = expires_at.strftime("%Y-%m-%d %H:%M UTC") if isinstance(expires_at, datetime) else (
        f"Request ဖန်တီးပြီး {int(APPROVAL_TTL.total_seconds() // 60)} မိနစ်အတွင်း"
    )
    status_text = "ဒုတိယ EC ၏ ခွင့်ပြုချက် စောင့်နေသည်" if pending else "Approval မတင်ရသေးပါ"
    return (
        "<b>⚠️ NUKE — Fund အပြီးဖျက်ရန် Approval</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"<b>Major:</b> {_safe(str(request.get('major', '')).upper())}\n"
        f"<b>Fund အမည်:</b> {_safe(request.get('fund_name') or request.get('fund_id'))}\n"
        f"<b>Fund ID:</b> <code>{_safe(request.get('fund_id'))}</code>\n"
        f"<b>လက်ရှိ Status:</b> {_safe(str(request.get('fund_status') or 'active').upper())}\n"
        f"<b>တစ်ဦးချင်း သတ်မှတ်ငွေ:</b> {_money(request.get('target_amount'))}\n"
        f"<b>paid_funds data ပါဝင်သည့် ကျောင်းသား:</b> {int(request.get('paid_student_count', 0))} ဦး\n"
        f"<b>အဲဒီ Fund အတွက် paid total:</b> {_money(request.get('paid_amount_total'))}\n"
        f"<b>ထိန်းသိမ်းထားမည့် payment_history entries:</b> {int(request.get('payment_history_count', 0))}\n"
        f"<b>Request တင်သူ ID:</b> <code>{int(request.get('requested_by', 0))}</code>\n"
        f"<b>Request ID:</b> <code>{_safe(request.get('request_id'))}</code>\n"
        f"<b>အခြေအနေ:</b> {status_text}\n"
        f"<b>သက်တမ်းကုန်ချိန်:</b> {expiry_text}\n\n"
        "<b>Approve လုပ်ပြီးပါက Fund record ကို database ထဲမှ အပြီးဖျက်ပြီး၊ သက်ဆိုင်ရာ ကျောင်းသားများ၏ "
        "<code>paid_funds</code> ထဲက ဒီ Fund value ကို ဖယ်ရှားပါမယ်။</b>\n"
        "Payment history, fund_transactions နဲ့ audit logs တွေကိုတော့ စာရင်းစစ်နိုင်ရန် မဖျက်ဘဲ ထားပါမယ်။\n"
        "ဒုတိယ active EC တစ်ဦးက ခွင့်ပြုမှသာ လုပ်ဆောင်ပါမယ်။"
    )


def _fund_list_query(major: str) -> dict:
    # Match /funds' scope so the EC can select the exact records visible there,
    # including legacy untagged CEIT funds; exclude only internal opening balances.
    return {"$and": [build_major_filter(major), {"type": {"$ne": "opening_balance"}}]}


async def _show_fund_page(message, db, major: str, requester_id: int, page: int, *, edit: bool):
    funds = await db.funds.find(_fund_list_query(major)).sort("created_at", -1).to_list(length=None)
    if not funds:
        text = f"📭 [{major.upper()}] အတွက် ဖျက်ရန် Fund မရှိပါ။"
        return await (message.edit_text(text) if edit else message.reply_text(text))
    pages = max(1, (len(funds) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(int(page), pages - 1))
    text = (
        f"<b>[{_safe(major.upper())}] အပြီးဖျက်မည့် Fund ကိုရွေးပါ</b>\n"
        f"စာမျက်နှာ {page + 1}/{pages} · Fund စုစုပေါင်း {len(funds)} ခု\n\n"
        "Active, Archived, Completed နဲ့ Cancelled အားလုံးကို ပြထားပါတယ်။ Fund တစ်ခုရွေးပြီး preview စစ်ပါ။\n"
        "ဒုတိယ EC approve မလုပ်မချင်း ဘာ data မှ မဖျက်ပါ။"
    )
    markup = fund_picker_keyboard(funds, page, pages, requester_id, PAGE_SIZE)
    if edit:
        return await message.edit_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
    return await message.reply_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)


async def _notify_requester(context: ContextTypes.DEFAULT_TYPE, request: dict, text: str, *, parse_mode=None) -> None:
    try:
        kwargs = {"chat_id": int(request["requested_by"]), "text": text}
        if parse_mode:
            kwargs["parse_mode"] = parse_mode
        await context.bot.send_message(**kwargs)
    except Exception as exc:
        logger.info("Could not notify NUKE requester %s: %s", request.get("requested_by"), exc)


async def _audit(db, *, major: str, actor_id: int, action: str, request: dict, details: dict | None = None) -> None:
    try:
        await log_audit(
            db,
            major=major,
            actor_id=actor_id,
            action=action,
            entity_type="nuke_fund_deletion_request",
            entity_id=str(request.get("request_id", "")),
            target_name=str(request.get("fund_name") or request.get("fund_id") or ""),
            target_user_id=request.get("requested_by"),
            details={
                "fund_id": request.get("fund_id"),
                "fund_doc_id": request.get("fund_doc_id"),
                "fund_status": request.get("fund_status"),
                "paid_student_count": request.get("paid_student_count", 0),
                **(details or {}),
            },
        )
    except Exception:
        logger.exception("Could not audit NUKE action %s", action)


async def nuke_funds_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show actual Fund documents; deletion is possible only after second-EC approval."""
    if not update.message or not update.effective_user:
        return
    user_id = int(update.effective_user.id)
    db = get_db()
    major = normalize_major(await get_user_major(user_id))
    if db is None:
        return await update.message.reply_text("❌ Database ချိတ်ဆက်မရပါ။ နောက်မှ ပြန်စမ်းပါ။")
    if not major or not await is_active_ec(db, user_id, major):
        return await update.message.reply_text("⛔ ဒီ command ကို သက်ဆိုင်ရာ Major ရဲ့ active EC များသာ အသုံးပြုနိုင်ပါတယ်။")

    existing = await db[COLLECTION_NAME].find_one({
        "major": major,
        "requested_by": user_id,
        "status": {"$in": ["draft", "pending"]},
        "expires_at": {"$gt": now_utc_naive()},
    })
    if existing:
        markup = pending_keyboard(existing["request_id"]) if existing.get("status") == "pending" else draft_keyboard(existing["request_id"])
        return await update.message.reply_text(_preview_text(existing, pending=existing.get("status") == "pending"), reply_markup=markup, parse_mode=ParseMode.HTML)

    return await _show_fund_page(update.message, db, major, user_id, 0, edit=False)


async def nuke_approval_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.from_user or not query.data:
        return
    parts = query.data.split(":")
    if len(parts) < 3 or parts[0] != "nuke":
        return await query.answer("NUKE action မမှန်ပါ။", show_alert=True)
    action = parts[1]
    actor_id = int(query.from_user.id)
    db = get_db()
    if db is None:
        return await query.answer("Database ချိတ်ဆက်မရပါ။", show_alert=True)

    if action in {"stop", "list", "pick"}:
        expected_parts = 3 if action == "stop" else 4
        if len(parts) != expected_parts:
            return await query.answer("Menu data မမှန်ပါ။", show_alert=True)
        try:
            owner_id = int(parts[2] if action == "stop" else parts[3])
        except ValueError:
            return await query.answer("Menu data မမှန်ပါ။", show_alert=True)
        if actor_id != owner_id:
            return await query.answer("ဒီ menu ကို ဖွင့်ထားသူသာ ရွေးချယ်နိုင်ပါတယ်။", show_alert=True)
        major = normalize_major(await get_user_major(actor_id))
        if not major or not await is_active_ec(db, actor_id, major):
            return await query.answer("သက်ဆိုင်ရာ Major ရဲ့ active EC မဟုတ်ပါ။", show_alert=True)
        if action == "stop":
            await query.answer()
            return await query.edit_message_text("✅ NUKE menu ကို ပိတ်လိုက်ပါပြီ။ Data မပြောင်းလဲပါ။")
        if action == "list":
            try:
                page = max(0, int(parts[2]))
            except ValueError:
                return await query.answer("Page number မမှန်ပါ။", show_alert=True)
            await query.answer()
            return await _show_fund_page(query.message, db, major, actor_id, page, edit=True)

        fund_doc_id = parts[2]
        try:
            request = await create_draft(db, major, actor_id, fund_doc_id)
        except (NukeApprovalError, ValueError) as exc:
            return await query.answer(str(exc), show_alert=True)
        await _audit(db, major=major, actor_id=actor_id, action="nuke_fund_requested", request=request,
                     details={"preview_hash": request.get("snapshot_hash")})
        await query.answer()
        return await query.edit_message_text(
            _preview_text(request, pending=request.get("status") == "pending"),
            reply_markup=pending_keyboard(request["request_id"]) if request.get("status") == "pending" else draft_keyboard(request["request_id"]),
            parse_mode=ParseMode.HTML,
        )

    if len(parts) != 3:
        return await query.answer("Request ID မပါပါ။", show_alert=True)
    request_id = parts[2]
    request = await get_request(db, request_id)
    if not request:
        return await query.answer("ဒီ request ကို ရှာမတွေ့ပါ။", show_alert=True)
    major = normalize_major(request.get("major"))
    if not major:
        return await query.answer("Request ထဲက Major မမှန်ပါ။ Admin ကိုအသိပေးပါ။", show_alert=True)

    if action == "cancel":
        cancelled = await cancel_request(db, request_id, actor_id)
        if not cancelled:
            return await query.answer("Request တင်သူမဟုတ်ပါက သို့မဟုတ် request ကို ကိုင်တွယ်ပြီးသားဖြစ်ပါက cancel မလုပ်နိုင်ပါ။", show_alert=True)
        await _audit(db, major=major, actor_id=actor_id, action="nuke_fund_cancelled", request=cancelled)
        await query.answer()
        return await query.edit_message_text("✅ NUKE request ကို ပယ်ဖျက်ပြီးပါပြီ။ Data မပြောင်းလဲပါ။")

    if action == "submit":
        try:
            pending, approver_ids = await submit_for_approval(db, request_id, actor_id)
        except NukeSnapshotChanged as exc:
            await _audit(db, major=major, actor_id=actor_id, action="nuke_fund_snapshot_changed", request=request)
            await query.answer()
            return await query.edit_message_text(f"⚠️ {_safe(str(exc))}")
        except (NukePermissionError, NukeRequestUnavailable, NukeNoApprover, NukeApprovalError) as exc:
            return await query.answer(str(exc), show_alert=True)

        notification_text = _preview_text(pending, pending=True) + "\n\n<b>Request တင်သူမဟုတ်သော active EC တစ်ဦးသာ Approve/Reject လုပ်နိုင်ပါတယ်။</b>"
        delivered_to: list[int] = []
        delivered_groups: list[int] = []
        for approver_id in approver_ids:
            try:
                await context.bot.send_message(chat_id=approver_id, text=notification_text,
                    reply_markup=approver_keyboard(request_id), parse_mode=ParseMode.HTML)
                delivered_to.append(approver_id)
            except Exception as exc:
                logger.info("Could not DM NUKE request %s to EC %s: %s", request_id, approver_id, exc)
        if not delivered_to:
            for group_id in MAJOR_GROUP_IDS.get(major, []):
                try:
                    await context.bot.send_message(chat_id=group_id, text=notification_text,
                        reply_markup=approver_keyboard(request_id), parse_mode=ParseMode.HTML)
                    delivered_groups.append(int(group_id))
                except Exception as exc:
                    logger.info("Could not post NUKE request %s to group %s: %s", request_id, group_id, exc)
        if not delivered_to and not delivered_groups:
            await revert_if_undelivered(db, request_id)
            await query.answer()
            return await query.edit_message_text(
                "⚠️ ဒုတိယ EC ထံ Approval request မပို့နိုင်ပါ။ EC များ Bot ကို အရင်ဖွင့်ထားကြောင်း စစ်ပြီး ပြန်တင်ပါ။",
                reply_markup=draft_keyboard(request_id),
            )
        await record_delivery(db, request_id, delivered_to, delivered_groups)
        latest = await get_request(db, request_id) or pending
        await _audit(db, major=major, actor_id=actor_id, action="nuke_fund_submitted", request=latest,
                     details={"notified_to": delivered_to, "notified_to_groups": delivered_groups})
        terminal_messages = {
            "executed": "✅ ဒီ Fund ကို အခြား EC က approve လုပ်ပြီး အပြီးဖျက်ပြီးပါပြီ။ Payment history ကို ထိန်းသိမ်းထားပါတယ်။",
            "rejected": "❌ ဒီ request ကို ဒုတိယ EC က ပယ်ချလိုက်ပါတယ်။ Data မပြောင်းလဲပါ။",
            "cancelled": "ဒီ request ကို ပယ်ဖျက်လိုက်ပါပြီ။ Data မပြောင်းလဲပါ။",
            "expired": "⌛ ဒီ request သက်တမ်းကုန်သွားပါပြီ။ /nuke ကို ပြန်စပါ။",
            "failed": "❌ ဒီ request ကို execute မလုပ်နိုင်ပါ။ Admin က စစ်ဆေးဖို့လိုပါတယ်။",
        }
        if latest.get("status") in terminal_messages:
            await query.answer("Request အခြေအနေ ပြောင်းလဲသွားပါတယ်။")
            return await query.edit_message_text(terminal_messages[latest["status"]])
        await query.answer("Approval request ပို့ပြီးပါပြီ။")
        return await query.edit_message_text(_preview_text(latest, pending=True), reply_markup=pending_keyboard(request_id), parse_mode=ParseMode.HTML)

    if action == "reject":
        try:
            rejected = await reject_request(db, request_id, actor_id)
        except NukePermissionError as exc:
            return await query.answer(str(exc), show_alert=True)
        except NukeRequestUnavailable as exc:
            await expire_request_if_due(db, request_id, "pending")
            return await query.answer(str(exc), show_alert=True)
        except Exception:
            logger.exception("NUKE reject failed for request %s", request_id)
            return await query.answer("Reject မလုပ်နိုင်ပါ။ MongoDB transaction support ကို စစ်ဆေးပါ။", show_alert=True)
        await _audit(db, major=major, actor_id=actor_id, action="nuke_fund_rejected", request=rejected,
                     details={"rejected_by": actor_id})
        await _notify_requester(context, rejected, "❌ ဒုတိယ EC က Fund deletion request ကို ပယ်ချလိုက်ပါတယ်။ Data မပြောင်းလဲပါ။")
        await query.answer("Request ကို reject လုပ်ပြီးပါပြီ။")
        return await query.edit_message_text("❌ Request ကို ပယ်ချပြီးပါပြီ။ Fund မဖျက်ထားပါ။")

    if action == "approve":
        try:
            completed = await execute_approved_cleanup(db, request_id, actor_id)
        except NukePermissionError as exc:
            current = await get_request(db, request_id)
            if current and "requester is no longer" in str(exc).lower():
                await db[COLLECTION_NAME].update_one(
                    {"request_id": request_id, "status": "pending"},
                    {"$set": {"status": "cancelled", "cancel_reason": "requester_permission_revoked", "cancelled_at": now_utc_naive()}},
                )
            return await query.answer(str(exc), show_alert=True)
        except NukeSnapshotChanged as exc:
            await db[COLLECTION_NAME].update_one(
                {"request_id": request_id, "status": "pending"},
                {"$set": {"status": "cancelled", "cancel_reason": "target_snapshot_changed", "cancelled_at": now_utc_naive()}},
            )
            await _notify_requester(context, request, f"⚠️ {_safe(str(exc))} Fund ကို မဖျက်ထားပါ။ /nuke ကို ပြန်စပါ။", parse_mode=ParseMode.HTML)
            await _audit(db, major=major, actor_id=actor_id, action="nuke_fund_snapshot_changed", request=request)
            await query.answer()
            return await query.edit_message_text("⚠️ Fund data ပြောင်းလဲသွားလို့ request ကိုပိတ်လိုက်ပါတယ်။ Fund မဖျက်ထားပါ။")
        except NukeRequestUnavailable as exc:
            await expire_request_if_due(db, request_id, "pending")
            return await query.answer(str(exc), show_alert=True)
        except Exception:
            logger.exception("NUKE Fund deletion failed for request %s", request_id)
            await _notify_requester(context, request, "❌ Fund ကို အပြီးဖျက်မရပါ။ Database transaction မအောင်မြင်ပါ။ Admin က transaction support ကို စစ်ဆေးပါ။")
            return await query.answer("NUKE execution မအောင်မြင်ပါ။ Fund ကို မဖျက်ထားသင့်ပါ။ MongoDB transaction support ကို စစ်ပါ။", show_alert=True)

        done_text = (
            f"✅ <b>NUKE — Fund အပြီးဖျက်ပြီးပါပြီ [{_safe(major.upper())}]</b>\n"
            f"Fund: <b>{_safe(completed.get('fund_name') or completed.get('fund_id'))}</b>\n"
            f"Fund ID: <code>{_safe(completed.get('fund_id'))}</code>\n"
            f"Request တင်သူ ID: <code>{int(completed['requested_by'])}</code>\n"
            f"Approve လုပ်သူ ID: <code>{int(completed['approved_by'])}</code>\n"
            f"ဖျက်လိုက်သော Fund records: <b>{int(completed.get('deleted_funds', 0))}</b> ခု\n"
            f"ဖယ်ရှားလိုက်သော student paid_funds entries: <b>{int(completed.get('affected_student_paid_funds', 0))}</b> ဦး\n"
            "Payment history, fund_transactions နဲ့ audit logs များကို မဖျက်ဘဲ ထိန်းသိမ်းထားပါတယ်။"
        )
        await _audit(db, major=major, actor_id=actor_id, action="nuke_fund_approved_and_executed", request=completed,
                     details={"approved_by": actor_id, "deleted_funds": completed.get("deleted_funds", 0),
                              "affected_student_paid_funds": completed.get("affected_student_paid_funds", 0)})
        await _notify_requester(context, completed, done_text, parse_mode=ParseMode.HTML)
        await query.answer("Approved — Fund permanently deleted.")
        return await query.edit_message_text(done_text, parse_mode=ParseMode.HTML)

    return await query.answer("မသိသော NUKE action ဖြစ်ပါတယ်။", show_alert=True)
