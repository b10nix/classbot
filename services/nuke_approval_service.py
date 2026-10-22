"""Database-backed two-person approval for permanent fund deletion.

The destructive operation removes one selected fund document and that fund's
embedded paid_funds entries from students of the selected major. It deliberately
retains payment_history, fund_transactions, and audit logs as financial history.
MongoDB transactions are required; unsupported deployments fail closed.
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from bson import ObjectId
from pymongo import ReturnDocument

from services.finance_service import build_major_filter, extract_paid_amount
from services.major_service import normalize_major

logger = logging.getLogger(__name__)

COLLECTION_NAME = "nuke_fund_deletion_requests"
APPROVAL_TTL = timedelta(minutes=15)
EXCLUDED_FUND_TYPES = {"opening_balance"}


class NukeApprovalError(Exception):
    """Base exception for expected workflow errors."""


class NukePermissionError(NukeApprovalError):
    """The actor is not an eligible active EC for this major."""


class NukeRequestUnavailable(NukeApprovalError):
    """The request is missing, expired, or already handled."""


class NukeSnapshotChanged(NukeApprovalError):
    """The selected fund or the data to be removed changed after preview."""


class NukeNoApprover(NukeApprovalError):
    """No second active EC is available to approve the request."""


def now_utc_naive() -> datetime:
    """Return UTC-naive datetime to match MongoDB's default BSON datetime behavior."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _fund_db_id(raw_id: str):
    """Convert stored string IDs back to ObjectId when applicable."""
    text = str(raw_id or "").strip()
    if ObjectId.is_valid(text):
        return ObjectId(text)
    return text


def _fund_query(major: str, fund_doc_id: str) -> dict[str, Any]:
    canonical = normalize_major(major)
    if not canonical or not fund_doc_id:
        raise ValueError("A valid major and fund document ID are required.")
    return {"$and": [
        build_major_filter(canonical),
        {"_id": _fund_db_id(fund_doc_id)},
        {"type": {"$ne": "opening_balance"}},
    ]}


def fund_snapshot(fund: dict[str, Any]) -> dict[str, Any]:
    """Stable, user-relevant fields used to detect a changed/deleted fund."""
    return {
        "_id": str(fund.get("_id")),
        "fund_id": str(fund.get("fund_id") or ""),
        "fund_name": str(fund.get("fund_name") or fund.get("title") or fund.get("fund_id") or ""),
        "title": fund.get("title"),
        "major": fund.get("major"),
        "status": str(fund.get("status") or "active").casefold(),
        "target_amount": int(fund.get("target_amount") or 0),
        "semester": fund.get("semester"),
        "academic_year": fund.get("academic_year"),
        "assignment_mode": fund.get("assignment_mode"),
        "managed_by": fund.get("managed_by"),
        "manager_user_id": fund.get("manager_user_id"),
        "created_by": fund.get("created_by"),
        "created_at": fund.get("created_at"),
    }


def _snapshot_digest(fund: dict[str, Any], students: list[dict[str, Any]], fund_id: str) -> str:
    wanted = str(fund_id).casefold()
    rows = []
    for student in students:
        paid_funds = student.get("paid_funds")
        if not isinstance(paid_funds, dict):
            continue
        matching = {str(k): v for k, v in paid_funds.items() if str(k).casefold() == wanted}
        if matching:
            rows.append({"id": str(student.get("_id")), "entries": matching})
    rows.sort(key=lambda row: row["id"])
    payload = {"fund": fund_snapshot(fund), "paid_fund_rows": rows}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def _load_fund(db, major: str, fund_doc_id: str, *, session=None) -> dict[str, Any] | None:
    return await db.funds.find_one(_fund_query(major, fund_doc_id), session=session)


async def _scan_students(db, major: str, *, session=None) -> list[dict[str, Any]]:
    cursor = db.student_info.find(
        build_major_filter(major),
        {"_id": 1, "major": 1, "paid_funds": 1, "payment_history": 1},
        session=session,
    )
    return await cursor.to_list(length=None)


def _fund_impact(students: list[dict[str, Any]], fund_id: str) -> dict[str, int]:
    wanted = str(fund_id).casefold()
    paid_student_count = 0
    paid_amount_total = 0
    history_entry_count = 0
    for student in students:
        paid_funds = student.get("paid_funds")
        if isinstance(paid_funds, dict):
            matching = [v for key, v in paid_funds.items() if str(key).casefold() == wanted]
            if matching:
                paid_student_count += 1
                paid_amount_total += sum(extract_paid_amount(value) for value in matching)
        history = student.get("payment_history")
        if isinstance(history, list):
            history_entry_count += sum(
                1 for entry in history
                if isinstance(entry, dict) and str(entry.get("fund_id") or "").casefold() == wanted
            )
    return {
        "paid_student_count": paid_student_count,
        "paid_amount_total": paid_amount_total,
        "payment_history_count": history_entry_count,
    }


async def is_active_ec(db, user_id: int, major: str, *, session=None) -> bool:
    """Require an explicit, active EC role for exactly this major."""
    canonical = normalize_major(major)
    if db is None or not canonical:
        return False
    role = await db.ec_roles.find_one(
        {
            "user_id": int(user_id),
            "major": canonical,
            "role": "ec",
            "active": {"$ne": False},
        },
        session=session,
    )
    return role is not None


async def get_active_approver_ids(db, major: str, requester_id: int) -> list[int]:
    canonical = normalize_major(major)
    if db is None or not canonical:
        return []
    rows = await db.ec_roles.find({
        "major": canonical,
        "role": "ec",
        "active": {"$ne": False},
        "user_id": {"$exists": True},
    }).to_list(length=None)
    approvers: list[int] = []
    for row in rows:
        try:
            uid = int(row.get("user_id"))
        except (TypeError, ValueError):
            continue
        if uid != int(requester_id) and uid not in approvers:
            approvers.append(uid)
    return approvers


async def create_draft(db, major: str, requester_id: int, fund_doc_id: str) -> dict[str, Any]:
    canonical = normalize_major(major)
    fund_doc_id = str(fund_doc_id or "").strip()
    if db is None or not canonical or not fund_doc_id:
        raise NukeApprovalError("Database, major, or selected Fund is unavailable.")
    if not await is_active_ec(db, requester_id, canonical):
        raise NukePermissionError("Only an active EC of this major can request permanent Fund deletion.")

    fund = await _load_fund(db, canonical, fund_doc_id)
    if not fund:
        raise NukeRequestUnavailable("Selected Fund could not be found in this major.")
    actual_fund_id = str(fund.get("fund_id") or "").strip()
    if not actual_fund_id:
        raise NukeApprovalError("This Fund has no fund_id. It cannot be safely deleted by this workflow.")

    collection = db[COLLECTION_NAME]
    await collection.create_index("request_id", unique=True, name="nuke_fund_deletion_request_id_unique")
    now = now_utc_naive()
    existing = await collection.find_one({
        "major": canonical,
        "requested_by": int(requester_id),
        "status": {"$in": ["draft", "pending"]},
        "expires_at": {"$gt": now},
    })
    if existing:
        if str(existing.get("fund_doc_id")) == fund_doc_id:
            return existing
        raise NukeApprovalError("သင့်မှာ NUKE request တစ်ခုရှိပြီးသားပါ။ အဲဒါကို Cancel လုပ်ပြီးမှ Fund အသစ်ရွေးပါ။")

    students = await _scan_students(db, canonical)
    impact = _fund_impact(students, actual_fund_id)
    request = {
        "request_id": secrets.token_hex(6),
        "major": canonical,
        "requested_by": int(requester_id),
        "fund_doc_id": fund_doc_id,
        "fund_id": actual_fund_id,
        "fund_snapshot": fund_snapshot(fund),
        "fund_name": str(fund.get("fund_name") or fund.get("title") or actual_fund_id),
        "fund_status": str(fund.get("status") or "active").casefold(),
        "target_amount": int(fund.get("target_amount") or 0),
        **impact,
        "snapshot_hash": _snapshot_digest(fund, students, actual_fund_id),
        "status": "draft",
        "created_at": now,
        "expires_at": now + APPROVAL_TTL,
    }
    await collection.insert_one(request)
    return request


async def get_request(db, request_id: str) -> dict[str, Any] | None:
    if db is None or not request_id:
        return None
    return await db[COLLECTION_NAME].find_one({"request_id": request_id})


async def _revalidate_snapshot(db, request: dict[str, Any], *, session=None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    major = normalize_major(request.get("major"))
    fund = await _load_fund(db, major, str(request.get("fund_doc_id") or ""), session=session)
    if not fund:
        raise NukeSnapshotChanged("ရွေးထားတဲ့ Fund ကို ဖျက်ပြီးသား သို့မဟုတ် မတွေ့တော့ပါ။")
    fund_id = str(fund.get("fund_id") or "").strip()
    students = await _scan_students(db, major, session=session)
    digest = _snapshot_digest(fund, students, fund_id)
    if digest != request.get("snapshot_hash"):
        raise NukeSnapshotChanged("Fund သို့မဟုတ် သက်ဆိုင်ရာ paid_funds data ပြောင်းလဲသွားလို့ request ကို ပယ်ဖျက်ပါမယ်။")
    return fund, students


async def submit_for_approval(db, request_id: str, requester_id: int) -> tuple[dict[str, Any], list[int]]:
    request = await get_request(db, request_id)
    if not request:
        raise NukeRequestUnavailable("This approval request could not be found.")
    if int(request.get("requested_by", 0)) != int(requester_id):
        raise NukePermissionError("Only the requester can submit this request for approval.")
    if request.get("status") != "draft":
        raise NukeRequestUnavailable("This request has already been submitted or handled.")
    now = now_utc_naive()
    if request.get("expires_at") and request["expires_at"] <= now:
        await db[COLLECTION_NAME].update_one(
            {"request_id": request_id, "status": "draft"},
            {"$set": {"status": "expired", "expired_at": now}},
        )
        raise NukeRequestUnavailable("The preview has expired. Start /nuke again.")
    major = normalize_major(request.get("major"))
    if not await is_active_ec(db, requester_id, major):
        raise NukePermissionError("The requester is no longer an active EC for this major.")
    try:
        await _revalidate_snapshot(db, request)
    except NukeSnapshotChanged:
        await db[COLLECTION_NAME].update_one(
            {"request_id": request_id, "status": "draft"},
            {"$set": {
                "status": "cancelled",
                "cancel_reason": "target_snapshot_changed_before_submit",
                "cancelled_at": now_utc_naive(),
            }},
        )
        raise
    approver_ids = await get_active_approver_ids(db, major, requester_id)
    if not approver_ids:
        raise NukeNoApprover("No second active EC is available for this major.")
    pending = await db[COLLECTION_NAME].find_one_and_update(
        {"request_id": request_id, "status": "draft", "requested_by": int(requester_id), "expires_at": {"$gt": now}},
        {"$set": {"status": "pending", "submitted_at": now, "expires_at": now + APPROVAL_TTL}},
        return_document=ReturnDocument.AFTER,
    )
    if not pending:
        raise NukeRequestUnavailable("Request state changed. Please run /nuke again.")
    return pending, approver_ids


async def record_delivery(db, request_id: str, delivered_to: list[int], delivered_groups: list[int]) -> None:
    await db[COLLECTION_NAME].update_one(
        {"request_id": request_id, "status": "pending"},
        {"$set": {"notified_to": delivered_to, "notified_to_groups": delivered_groups, "notification_sent": True}},
    )


async def revert_if_undelivered(db, request_id: str) -> bool:
    result = await db[COLLECTION_NAME].update_one(
        {"request_id": request_id, "status": "pending"},
        {"$set": {"status": "draft", "delivery_error": True, "expires_at": now_utc_naive() + APPROVAL_TTL}},
    )
    return bool(result.modified_count)


async def cancel_request(db, request_id: str, actor_id: int) -> dict[str, Any] | None:
    now = now_utc_naive()
    return await db[COLLECTION_NAME].find_one_and_update(
        {"request_id": request_id, "requested_by": int(actor_id), "status": {"$in": ["draft", "pending"]}},
        {"$set": {"status": "cancelled", "cancelled_at": now, "cancelled_by": int(actor_id)}},
        return_document=ReturnDocument.AFTER,
    )


async def reject_request(db, request_id: str, approver_id: int) -> dict[str, Any]:
    request = await get_request(db, request_id)
    if not request:
        raise NukeRequestUnavailable("This approval request could not be found.")
    major = normalize_major(request.get("major"))
    if not major:
        raise NukeRequestUnavailable("Invalid major stored in approval request.")
    if int(request.get("requested_by", 0)) == int(approver_id):
        raise NukePermissionError("The requester cannot approve or reject their own request.")
    try:
        async with await db.client.start_session() as session:
            async with session.start_transaction():
                if not await is_active_ec(db, approver_id, major, session=session):
                    raise NukePermissionError("Only an active EC for this major can reject this request.")
                updated = await db[COLLECTION_NAME].find_one_and_update(
                    {"request_id": request_id, "status": "pending", "expires_at": {"$gt": now_utc_naive()}, "requested_by": {"$ne": int(approver_id)}},
                    {"$set": {"status": "rejected", "rejected_by": int(approver_id), "rejected_at": now_utc_naive()}},
                    return_document=ReturnDocument.AFTER,
                    session=session,
                )
                if not updated:
                    raise NukeRequestUnavailable("This request was already handled or has expired.")
        return updated
    except (NukePermissionError, NukeRequestUnavailable):
        raise
    except Exception:
        logger.exception("Failed to reject fund-deletion request %s", request_id)
        raise


async def expire_request_if_due(db, request_id: str, expected_status: str = "pending") -> bool:
    result = await db[COLLECTION_NAME].update_one(
        {"request_id": request_id, "status": expected_status, "expires_at": {"$lte": now_utc_naive()}},
        {"$set": {"status": "expired", "expired_at": now_utc_naive()}},
    )
    return bool(result.modified_count)


async def execute_approved_cleanup(db, request_id: str, approver_id: int) -> dict[str, Any]:
    """Atomically revalidate, remove selected fund + paid_funds values, and finalize."""
    request = await get_request(db, request_id)
    if not request:
        raise NukeRequestUnavailable("This approval request could not be found.")
    major = normalize_major(request.get("major"))
    if not major:
        raise NukeRequestUnavailable("Invalid major stored in approval request.")
    requester_id = int(request.get("requested_by", 0))
    if requester_id == int(approver_id):
        raise NukePermissionError("The requester cannot approve their own request.")

    try:
        async with await db.client.start_session() as session:
            async with session.start_transaction():
                now = now_utc_naive()
                if not await is_active_ec(db, approver_id, major, session=session):
                    raise NukePermissionError("You are not an active EC for this major.")
                if not await is_active_ec(db, requester_id, major, session=session):
                    raise NukePermissionError("The requester is no longer an active EC. The request cannot execute.")
                claimed = await db[COLLECTION_NAME].find_one_and_update(
                    {"request_id": request_id, "status": "pending", "expires_at": {"$gt": now}, "requested_by": {"$ne": int(approver_id)}},
                    {"$set": {"status": "executing", "approved_by": int(approver_id), "approved_at": now}},
                    return_document=ReturnDocument.AFTER,
                    session=session,
                )
                if not claimed:
                    raise NukeRequestUnavailable("This request was already handled or has expired.")

                fund, students = await _revalidate_snapshot(db, claimed, session=session)
                fund_id = str(fund.get("fund_id") or "").strip()
                paid_key = fund_id.casefold()
                affected_students = 0
                for student in students:
                    current_paid = student.get("paid_funds")
                    if not isinstance(current_paid, dict):
                        continue
                    matching_keys = [key for key in current_paid if str(key).casefold() == paid_key]
                    if not matching_keys:
                        continue
                    revised = dict(current_paid)
                    for key in matching_keys:
                        revised.pop(key, None)
                    update_result = await db.student_info.update_one(
                        {"_id": student["_id"], "paid_funds": current_paid},
                        {"$set": {"paid_funds": revised}},
                        session=session,
                    )
                    if int(getattr(update_result, "modified_count", 0)) != 1:
                        raise NukeSnapshotChanged("Student paid_funds ပြောင်းလဲသွားပါတယ်။ Operation ကို rollback လုပ်ထားပါတယ်။")
                    affected_students += 1

                delete_result = await db.funds.delete_one(_fund_query(major, str(fund.get("_id"))), session=session)
                if int(getattr(delete_result, "deleted_count", 0)) != 1:
                    raise NukeSnapshotChanged("Selected Fund ကို တိတိကျကျ မဖျက်နိုင်ပါ။ Operation ကို rollback လုပ်ထားပါတယ်။")

                finalized = await db[COLLECTION_NAME].update_one(
                    {"request_id": request_id, "status": "executing", "approved_by": int(approver_id)},
                    {"$set": {
                        "status": "executed",
                        "executed_at": now_utc_naive(),
                        "deleted_funds": 1,
                        "affected_student_paid_funds": affected_students,
                        "history_preserved": True,
                    }},
                    session=session,
                )
                if int(getattr(finalized, "modified_count", 0)) != 1:
                    raise NukeRequestUnavailable("Could not finalize the approval request.")
        completed = await get_request(db, request_id)
        if not completed:
            raise NukeRequestUnavailable("Fund deletion committed but request record could not be reloaded.")
        return completed
    except (NukePermissionError, NukeRequestUnavailable, NukeSnapshotChanged):
        raise
    except Exception:
        logger.exception("Fund NUKE transaction failed for request %s", request_id)
        await db[COLLECTION_NAME].update_one(
            {"request_id": request_id, "status": "pending"},
            {"$set": {"status": "failed", "failed_at": now_utc_naive(), "failure_reason": "transaction_failed"}},
        )
        raise
