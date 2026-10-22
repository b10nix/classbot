"""Major resolution, membership checks, EC permissions and safe migration helpers."""
from __future__ import annotations

import logging
import re
from typing import Any

from database.db import (
    get_db,
    get_shared_db,
    get_major_db,
    get_all_major_dbs,
    is_major_database_enabled,
)
from utils.constants import MAJORS, CLASS_GROUP_IDS, GROUP_DEFAULT_MAJORS
from utils.auth import is_super_admin
from services.roll_number_service import (
    VALID_MAJORS,
    MAJOR_ALIASES,
    RollNumberError,
    normalize_major_strict,
    parse_roll_number,
)

logger = logging.getLogger(__name__)
DEFAULT_MAJOR = "ceit"


def normalize_major(raw: str | None) -> str | None:
    if raw is None:
        return None
    return MAJOR_ALIASES.get(str(raw).strip().lower())


def extract_major_from_roll(roll_no: str, default: str | None = None) -> str | None:
    """Extract a major only when it is present in the roll string.

    The default is deliberately None: numeric-only roll numbers do not identify
    a major and must never be silently assigned to CEIT.
    """
    if not roll_no:
        return default
    try:
        parsed = parse_roll_number(str(roll_no))
        if parsed.get("major"):
            return parsed["major"]
    except RollNumberError:
        pass
    for part in re.split(r"[-_\s]+", str(roll_no).strip()):
        canonical = normalize_major(part)
        if canonical:
            return canonical
    return default


async def find_student_record_by_user_id(user_id: int) -> tuple[dict[str, Any] | None, str | None]:
    """Find a student's record across all configured major databases.

    Returns (document, major). In multi-database mode, the database itself can
    identify the major if an old document lacks a `major` field. If duplicate
    registrations exist in more than one major, returns (None, None) and logs
    a warning rather than choosing one arbitrarily.
    """
    if not is_major_database_enabled():
        shared = get_shared_db()
        if shared is None:
            return None, None
        doc = await shared.student_info.find_one({"$or": [{"tg_id": user_id}, {"user_id": user_id}]})
        if not doc:
            return None, None
        major = normalize_major(doc.get("major")) or extract_major_from_roll(doc.get("roll_no", ""))
        return doc, major

    matches: list[tuple[dict[str, Any], str]] = []
    for major, database in get_all_major_dbs():
        if database is None:
            continue
        doc = await database.student_info.find_one({"$or": [{"tg_id": user_id}, {"user_id": user_id}]})
        if doc:
            detected = normalize_major(doc.get("major")) or normalize_major(major)
            if detected:
                matches.append((doc, detected))

    unique_majors = {major for _, major in matches}
    if len(unique_majors) > 1:
        logger.warning("Telegram user %s has student records in multiple majors: %s", user_id, sorted(unique_majors))
        return None, None
    if not matches:
        return None, None
    return matches[0]


async def _build_major_group_mapping(shared) -> dict[int, str]:
    """Build the same authoritative group-to-major map used by registration checks."""
    group_to_major: dict[int, str] = {}
    for configured_gid, configured_majors in GROUP_DEFAULT_MAJORS.items():
        if len(configured_majors) == 1:
            group_to_major[configured_gid] = next(iter(configured_majors))

    # Database routing is an explicit admin override and takes precedence.
    route_docs = await shared.group_routes.find({}).to_list(length=None)
    for doc in route_docs:
        gid = doc.get("group_id")
        if gid is None:
            continue
        try:
            gid = int(gid)
        except (TypeError, ValueError):
            continue
        if gid in CLASS_GROUP_IDS:
            # Shared class groups must never identify an academic Major.
            continue
        primary = normalize_major(doc.get("primary_major"))
        routed = [normalize_major(m) for m in doc.get("majors", []) if normalize_major(m)]
        if primary:
            group_to_major[gid] = primary
        elif len(routed) == 1:
            group_to_major[gid] = routed[0]
    return group_to_major


async def resolve_user_major_candidates_from_groups(
    user_id: int, bot
) -> tuple[list[str], str | None]:
    """Return all verifiable Major candidates for a user registering in bot DM.

    One candidate can be selected automatically. Multiple candidates are returned
    so the registration conversation can ask the user to choose instead of failing
    with the older ambiguous-membership error. The bot must be able to inspect all
    configured Major groups to make a reliable single-vs-multiple decision.
    """
    shared = get_shared_db()
    if shared is None:
        return [], "❌ Database ချိတ်ဆက်မှု မအောင်မြင်ပါ။"

    if is_super_admin(user_id):
        settings = await shared.user_settings.find_one({"user_id": user_id})
        configured = normalize_major(settings.get("active_major")) if settings else None
        if configured:
            return [configured], None

    student, student_major = await find_student_record_by_user_id(user_id)
    if student and student_major:
        return [student_major], None

    role_docs = await shared.ec_roles.find({"user_id": user_id}).to_list(length=None)
    role_majors = {m for row in role_docs if (m := normalize_major(row.get("major")))}
    if len(role_majors) == 1:
        return [next(iter(role_majors))], None

    group_to_major = await _build_major_group_mapping(shared)
    if not group_to_major:
        return [], (
            "⚠️ <b>Major Group များကို မသတ်မှတ်ရသေးပါ။</b>\n"
            "Admin က `.env` ထဲရှိ Major Group ID များနှင့် Group Major mapping ကို စစ်ဆေးပေးပါ။"
        )

    mapped_majors = set(group_to_major.values())
    missing_majors = [major for major in MAJORS if major not in mapped_majors]
    if missing_majors:
        missing_text = ", ".join(major.upper() for major in missing_majors)
        return [], (
            "⚠️ <b>Major Group mapping မပြည့်စုံသေးပါ။</b>\n\n"
            f"Group ID မသတ်မှတ်ရသေးသော Major: <b>{missing_text}</b>\n"
            "Major ၇ ခုလုံးကို အတည်ပြုနိုင်မှသာ မိမိ GP တစ်ခုတည်းဝင်ထားသလား၊ "
            "GP အများအပြားဝင်ထားသလား ခွဲခြားနိုင်ပါမယ်။ Admin က `.env` / Group routing ကို စစ်ဆေးပေးပါ။"
        )

    matched: set[str] = set()
    failed_group_checks: list[int] = []
    if bot is None:
        failed_group_checks.extend(group_to_major)
    else:
        for group_id, major in group_to_major.items():
            try:
                member = await bot.get_chat_member(chat_id=group_id, user_id=user_id)
                status = getattr(member, "status", None)
                is_member = status in ("member", "administrator", "creator")
                if status == "restricted":
                    is_member = bool(getattr(member, "is_member", False))
                if is_member:
                    matched.add(major)
            except Exception as exc:
                failed_group_checks.append(group_id)
                logger.debug(
                    "Could not verify registration membership user=%s group=%s: %s",
                    user_id, group_id, exc,
                )

    # Never infer a single Major when any other configured group could not be checked.
    if failed_group_checks:
        return [], (
            "⚠️ <b>Major Group membership စစ်ဆေးမှု မပြည့်စုံပါ။</b>\n\n"
            "Bot ကို သက်ဆိုင်ရာ Major GP အားလုံးတွင် Admin အဖြစ် ထည့်ထားခြင်းရှိမရှိ၊ "
            "Group ID များမှန်မမှန်ကို Admin က စစ်ဆေးပေးရန် လိုအပ်ပါသည်။\n"
            "အချက်အလက် မသေချာသေးသောကြောင့် Registration ကို မဆက်လုပ်ထားပါ။"
        )

    # This is a self-registration trust decision: live Telegram membership is
    # authoritative. Do not fall back to shared.group_members when the API
    # successfully reports no membership, because that cache may be stale.
    if not matched:
        return [], (
            "⚠️ <b>သင်၏ Major ကို အတည်ပြု၍ မရပါ။</b>\n\n"
            "သက်ဆိုင်ရာ Major Telegram Group တွင် Member ဖြစ်ရန်နှင့် "
            "Group Major ကို Admin မှ သတ်မှတ်ထားရန် လိုအပ်ပါသည်။"
        )

    return sorted(matched, key=lambda m: MAJORS.index(m) if m in MAJORS else len(MAJORS)), None


async def resolve_user_major_from_groups(user_id: int, bot) -> tuple[str | None, str | None]:
    """Resolve a user's major by an unambiguous registered identity or group membership."""
    shared = get_shared_db()
    if shared is None:
        return None, "❌ Database ချိတ်ဆက်မှု မအောင်မြင်ပါ။"

    # Super Admin workspace is explicit and user-selected, not inferred from membership.
    if is_super_admin(user_id):
        settings = await shared.user_settings.find_one({"user_id": user_id})
        configured = normalize_major(settings.get("active_major")) if settings else None
        if configured:
            return configured, None

    # Existing student record, if unique across major DBs.
    student, student_major = await find_student_record_by_user_id(user_id)
    if student and student_major:
        return student_major, None

    # EC role has to be unambiguous for a user who has more than one EC assignment.
    role_docs = await shared.ec_roles.find({"user_id": user_id}).to_list(length=None)
    role_majors = {m for row in role_docs if (m := normalize_major(row.get("major")))}
    if len(role_majors) == 1:
        return next(iter(role_majors)), None
    if len(role_majors) > 1:
        names = ", ".join(sorted(m.upper() for m in role_majors))
        return None, f"⚠️ Major အများအပြားတွင် EC အဖြစ် သတ်မှတ်ထားသည် ({names})။ သက်ဆိုင်ရာ Major ကို Admin ထံ အတည်ပြုပါ။"

    group_to_major = await _build_major_group_mapping(shared)

    matched: set[str] = set()
    if bot is not None:
        for group_id, major in group_to_major.items():
            try:
                member = await bot.get_chat_member(chat_id=group_id, user_id=user_id)
                if member.status in ("member", "administrator", "creator", "restricted"):
                    matched.add(major)
            except Exception as exc:
                logger.debug("Could not verify membership for user=%s group=%s: %s", user_id, group_id, exc)

    if not matched and group_to_major:
        for row in await shared.group_members.find({"user_id": user_id}).to_list(length=None):
            try:
                group_id = int(row.get("chat_id"))
            except (TypeError, ValueError):
                continue
            if group_id in group_to_major:
                matched.add(group_to_major[group_id])

    if len(matched) == 1:
        return next(iter(matched)), None
    if len(matched) > 1:
        names = ", ".join(sorted(m.upper() for m in matched))
        return None, (
            "⚠️ <b>Major အတည်ပြု၍ မရပါ (Ambiguous Membership)</b>\n"
            f"သင်သည် Major Group အများအပြား ({names}) တွင် ပါဝင်နေပါသည်။\n"
            "သက်ဆိုင်ရာ Major EC သို့မဟုတ် Admin ထံ ဆက်သွယ်ပါ။"
        )
    return None, (
        "⚠️ <b>သင်၏ Major ကို အတည်ပြု၍ မရပါ။</b>\n\n"
        "သက်ဆိုင်ရာ Major Telegram Group တွင် Member ဖြစ်ရန်နှင့် Group Major ကို Admin မှ သတ်မှတ်ထားရန် လိုအပ်ပါသည်။"
    )


async def ensure_multi_major_migration() -> None:
    """Create indexes and backfill only values that are explicitly determinable.

    This intentionally does NOT tag missing-major finance records as CEIT and
    does not move data between databases. Use the reviewed migration utility for
    cross-database migration.
    """
    databases = get_all_major_dbs() if is_major_database_enabled() else [("legacy", get_shared_db())]
    for database_label, database in databases:
        if database is None:
            continue
        try:
            students = await database.student_info.find({
                "$or": [
                    {"major": {"$exists": False}},
                    {"internal_id": {"$exists": False}},
                    {"roll_number": {"$exists": False}},
                ]
            }).to_list(length=None)
            for student in students:
                roll = str(student.get("roll_no") or "")
                try:
                    parsed = parse_roll_number(roll)
                except RollNumberError:
                    parsed = None
                major = normalize_major(student.get("major"))
                if not major and parsed:
                    major = parsed.get("major")
                if not major and database_label != "legacy":
                    major = normalize_major(database_label)
                updates: dict[str, Any] = {}
                if major:
                    updates["major"] = major
                if not student.get("internal_id"):
                    updates["internal_id"] = f"stu_{student['_id']}"
                if parsed:
                    updates["roll_number"] = parsed["roll_number"]
                    updates["roll_number_str"] = parsed["roll_str"]
                    if parsed.get("semester") and not student.get("current_semester"):
                        updates["current_semester"] = parsed["semester"]
                if updates:
                    await database.student_info.update_one({"_id": student["_id"]}, {"$set": updates})

            await database.student_info.create_index([("major", 1), ("roll_number", 1)])
            await database.student_info.create_index([("major", 1), ("roll_no", 1)])
            await database.student_info.create_index([("internal_id", 1)])
            await database.academic_settings.create_index([("major", 1)], unique=True)
            await database.funds.create_index([("major", 1), ("fund_id", 1)])
            await database.ec_ledgers.create_index([("major", 1), ("ec_id", 1)])
            logger.info("Verified multi-major indexes/schema for %s", database_label)
        except Exception:
            logger.exception("Multi-major schema check failed for %s", database_label)


async def get_user_major(user_id: int) -> str | None:
    """Determine a single active major; return None when no safe answer exists."""
    shared = get_shared_db()
    if shared is None:
        return None

    if is_super_admin(user_id):
        settings = await shared.user_settings.find_one({"user_id": user_id})
        selected = normalize_major(settings.get("active_major")) if settings else None
        return selected or DEFAULT_MAJOR

    role_docs = await shared.ec_roles.find({"user_id": user_id}).to_list(length=None)
    role_majors = {m for row in role_docs if (m := normalize_major(row.get("major")))}
    if len(role_majors) == 1:
        return next(iter(role_majors))
    if len(role_majors) > 1:
        return None

    _, major = await find_student_record_by_user_id(user_id)
    if major:
        return major

    # No default fallback for ordinary users: callers must deny scoped operations.
    return None


async def is_major_authorized(user_id: int, major: str | None = None) -> bool:
    """Super Admins can manage any major; ECs only the major(s) assigned to them."""
    if is_super_admin(user_id):
        return True
    shared = get_shared_db()
    if shared is None:
        return False

    requested = normalize_major(major) if major is not None else None
    if major is not None and requested is None:
        return False
    query: dict[str, Any] = {"user_id": user_id}
    if requested:
        query["major"] = requested
    role_docs = await shared.ec_roles.find(query).to_list(length=None)
    role_majors = {m for row in role_docs if (m := normalize_major(row.get("major")))}
    if requested:
        if requested in role_majors:
            return True
    elif len(role_majors) == 1:
        return True

    return False


async def resolve_ec_in_major(identifier: str | int, major: str = DEFAULT_MAJOR) -> tuple[int | None, str]:
    raw_str = str(identifier).strip()
    clean_key = raw_str.lower().lstrip("@")
    try:
        canonical_major = normalize_major_strict(major)
    except RollNumberError:
        return None, raw_str

    shared = get_shared_db()
    if shared is not None:
        if clean_key.isdigit():
            role_doc = await shared.ec_roles.find_one({"user_id": int(clean_key), "major": canonical_major})
        else:
            role_doc = await shared.ec_roles.find_one({
                "major": canonical_major,
                "nickname": {"$regex": f"^{re.escape(clean_key)}$", "$options": "i"},
            })
        if role_doc:
            return int(role_doc["user_id"]), str(role_doc.get("nickname", clean_key))

    if clean_key.isdigit():
        return int(clean_key), raw_str

    target_db = get_db(major=canonical_major)
    if target_db is not None:
        regex_q = {"$regex": f"^{re.escape(clean_key)}$", "$options": "i"}
        ledger_ec = await target_db.ec_ledgers.find_one({"username": regex_q})
        if ledger_ec:
            uid = ledger_ec.get("ec_id") or ledger_ec.get("user_id")
            if uid:
                return int(uid), str(ledger_ec.get("username", clean_key))

        student_ec = await target_db.student_info.find_one({
            "$or": [{"tg_username": regex_q}, {"name": regex_q}]
        })
        if student_ec:
            uid = student_ec.get("tg_id") or student_ec.get("user_id")
            if uid:
                return int(uid), str(student_ec.get("name") or clean_key)

    return None, raw_str


async def resolve_ec_nick_by_id_in_major(user_id: int, major: str = DEFAULT_MAJOR) -> str:
    canonical = normalize_major(major) or DEFAULT_MAJOR
    shared = get_shared_db()
    if shared is not None:
        role = await shared.ec_roles.find_one({"user_id": user_id, "major": canonical})
        if role and role.get("nickname"):
            return str(role["nickname"]).lower()

    return "ec"
