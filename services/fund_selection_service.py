"""Read-only helpers for selecting finance funds without typing Fund IDs."""
from __future__ import annotations

from typing import Any

from database.db import get_db
from services.finance_service import (
    build_fund_major_filter,
    build_major_filter,
    extract_paid_amount,
    payment_entry_fingerprint,
)
from services.major_service import normalize_major


def _fund_name(fund: dict[str, Any]) -> str:
    fund_id = str(fund.get("fund_id") or "")
    return str(fund.get("fund_name") or fund.get("title") or fund_id or "Unnamed fund")


async def list_fund_options(major: str, action: str) -> list[dict[str, Any]]:
    """Return normalized funds for an action, including legacy CEIT archive metadata."""
    canonical = normalize_major(major)
    db = get_db(major=canonical) if canonical else None
    if db is None or not canonical:
        return []

    query = {"$and": [build_fund_major_filter(canonical), {"type": {"$ne": "opening_balance"}}]}
    documents = await db.funds.find(query).sort([("created_at", -1), ("fund_id", 1)]).to_list(length=None)

    legacy_archived: set[str] = set()
    # Before fund documents carried status=archived, CEIT used sys_config as the
    # archive source. Keep those funds selectable for unarchive and hide them
    # from the archive picker so the inline UI matches the old storage behavior.
    if canonical == "ceit":
        try:
            legacy_cfg = await db.sys_config.find_one({"type": "archived_funds"}) or {}
            legacy_archived = {
                str(value).strip().casefold()
                for value in legacy_cfg.get("funds", [])
                if str(value).strip()
            }
        except Exception:
            legacy_archived = set()

    options: list[dict[str, Any]] = []
    seen: set[str] = set()
    for fund in documents:
        fund_id = str(fund.get("fund_id") or "").strip()
        key = fund_id.casefold()
        if not fund_id or key in seen:
            continue
        status = str(fund.get("status") or "active").casefold()
        is_archived = status == "archived" or key in legacy_archived
        if action == "unarchive" and not is_archived:
            continue
        if action == "archive" and (is_archived or status in {"deleted", "cancelled"}):
            continue
        if action not in {"archive", "unarchive"} and status == "deleted":
            continue
        seen.add(key)
        options.append({
            "fund_id": fund_id,
            "fund_name": _fund_name(fund),
            "target_amount": int(fund.get("target_amount") or 0),
            "status": "archived" if is_archived else status,
        })
    return options


async def find_fund_option(major: str, fund_id: str, action: str = "report") -> dict[str, Any] | None:
    wanted = str(fund_id or "").strip().casefold()
    for fund in await list_fund_options(major, action):
        if fund["fund_id"].casefold() == wanted:
            return fund
    return None


def build_undo_options(student: dict[str, Any], fund_names: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Build one option per fund with a positive current amount and undoable history."""
    fund_names = fund_names or {}
    paid_funds = student.get("paid_funds") if isinstance(student.get("paid_funds"), dict) else {}
    paid_by_key = {str(key).casefold(): value for key, value in paid_funds.items()}
    history = student.get("payment_history") if isinstance(student.get("payment_history"), list) else []
    newest_by_fund: dict[str, dict[str, Any]] = {}
    for entry in reversed(history):
        if not isinstance(entry, dict):
            continue
        fund_id = str(entry.get("fund_id") or "").strip()
        key = fund_id.casefold()
        if not fund_id or key in newest_by_fund:
            continue
        current_paid = extract_paid_amount(paid_by_key.get(key, 0))
        if current_paid <= 0:
            continue
        newest_by_fund[key] = {
            # Finance commands and paid_funds paths use lowercase fund IDs.
            "fund_id": key,
            "fund_name": fund_names.get(key, fund_id),
            "last_amount": int(entry.get("amount") or 0),
            "last_date": str(entry.get("date") or "Unknown date"),
            "transaction_id": str(entry.get("transaction_id") or ""),
            "transaction_fingerprint": payment_entry_fingerprint(entry),
            "current_paid": current_paid,
            "received_by": str(entry.get("received_by") or "Unknown EC"),
        }
    return sorted(newest_by_fund.values(), key=lambda row: row["fund_name"].casefold())


async def reminder_preview(major: str, fund_id: str) -> dict[str, Any] | None:
    """Count reminder targets without sending messages."""
    canonical = normalize_major(major)
    db = get_db(major=canonical) if canonical else None
    if db is None or not canonical:
        return None
    fund = await db.funds.find_one({
        "$and": [
            build_fund_major_filter(canonical),
            {"fund_id": {"$regex": f"^{_escape_regex(fund_id)}$", "$options": "i"}},
        ]
    })
    if not fund:
        return None

    actual_fund_id = str(fund.get("fund_id") or fund_id).lower()
    target_amount = int(fund.get("target_amount") or 0)
    raw_students = await db.student_info.find(build_major_filter(canonical)).to_list(length=None)
    unique: dict[str, dict[str, Any]] = {}
    for student in raw_students:
        roll = str(student.get("roll_no") or "").strip()
        if not roll:
            continue
        current = unique.get(roll)
        has_id = bool(student.get("user_id") or student.get("tg_id"))
        current_has_id = bool(current and (current.get("user_id") or current.get("tg_id")))
        if current is None or (not current_has_id and has_id):
            unique[roll] = student

    needs_payment = 0
    can_receive = 0
    no_telegram_id = 0
    for student in unique.values():
        paid_map = student.get("paid_funds") if isinstance(student.get("paid_funds"), dict) else {}
        paid_map_by_key = {str(key).casefold(): value for key, value in paid_map.items()}
        paid = extract_paid_amount(paid_map_by_key.get(actual_fund_id.casefold(), 0))
        if target_amount - paid <= 0:
            continue
        needs_payment += 1
        if student.get("user_id") or student.get("tg_id"):
            can_receive += 1
        else:
            no_telegram_id += 1

    return {
        "major": canonical,
        "fund_id": actual_fund_id,
        "fund_name": _fund_name(fund),
        "target_amount": target_amount,
        "students_scanned": len(unique),
        "needs_payment": needs_payment,
        "can_receive": can_receive,
        "no_telegram_id": no_telegram_id,
    }


def _escape_regex(value: str) -> str:
    import re
    return re.escape(str(value or ""))
