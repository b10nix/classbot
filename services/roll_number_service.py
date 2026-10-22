# services/roll_number_service.py

import re
import uuid
import logging
from datetime import datetime, timezone, timedelta
from database.db import get_db, get_all_major_dbs, is_major_database_enabled

logger = logging.getLogger(__name__)

# Canonical Major Codes
VALID_MAJORS = ("ceit", "ec", "ep", "civil", "mech", "mc", "archi")

MAJOR_ALIASES = {
    "it": "ceit",
    "ceit": "ceit",
    "ec": "ec",
    "ep": "ep",
    "civil": "civil",
    "c": "civil",
    "mech": "mech",
    "me": "mech",
    "mc": "mc",
    "archi": "archi",
    "arch": "archi",
}

ROMAN_TO_INT = {
    "I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6,
    "VII": 7, "VIII": 8, "IX": 9, "X": 10, "XI": 11, "XII": 12
}
INT_TO_ROMAN = {v: k for k, v in ROMAN_TO_INT.items()}

# Canonical regex: Sem-{ROMAN}-{MAJOR}-{NUMBER}
CANONICAL_ROLL_RE = re.compile(
    r"^Sem-(I|II|III|IV|V|VI|VII|VIII|IX|X|XI|XII)-([A-Za-z]+)-(\d{1,3})$",
    re.IGNORECASE
)
# Legacy Arabic semester regex: Sem-2-CEIT-01
LEGACY_ARABIC_ROLL_RE = re.compile(
    r"^Sem-(\d{1,2})-([A-Za-z]+)-(\d{1,3})$",
    re.IGNORECASE
)
# Short major prefix regex: CEIT-01 or EC-5
SHORT_MAJOR_ROLL_RE = re.compile(
    r"^([A-Za-z]+)-(\d{1,3})$",
    re.IGNORECASE
)


class RollNumberError(ValueError):
    """Raised when a roll number or semester input is invalid."""
    pass


class SemesterNotConfiguredError(RuntimeError):
    """Raised when a major does not have an active semester configured in academic_settings."""
    pass


class CrossMajorAccessError(PermissionError):
    """Raised when an input attempts to reference a different major without authorization."""
    pass


def get_mm_now_str() -> str:
    mm_tz = timezone(timedelta(hours=6, minutes=30))
    return datetime.now(mm_tz).strftime("%Y-%m-%d %I:%M:%S %p")


def normalize_major_strict(raw: str | None) -> str:
    """Strictly validates and normalizes a major code. Raises RollNumberError if invalid."""
    if not raw or not isinstance(raw, str):
        raise RollNumberError("Major code is required.")
    clean = raw.strip().lower()
    if clean not in MAJOR_ALIASES:
        valid_str = ", ".join(m.upper() for m in VALID_MAJORS)
        raise RollNumberError(f"Invalid major '{raw}'. Valid majors: {valid_str}")
    return MAJOR_ALIASES[clean]


def normalize_semester_input(semester: int | str) -> tuple[int, str]:
    """
    Validates a semester input (integer 1-12 or Roman numeral I-XII).
    Returns (semester_int, roman_str).
    """
    if isinstance(semester, bool) or semester is None:
        raise RollNumberError("Semester is required.")

    if isinstance(semester, int):
        if semester not in INT_TO_ROMAN:
            raise RollNumberError(f"Semester must be between 1 and 12 (got {semester}).")
        return semester, INT_TO_ROMAN[semester]

    raw = str(semester).strip().upper()
    if raw.startswith("SEM-"):
        raw = raw[4:]
    elif raw.startswith("SEM"):
        raw = raw[3:].strip()

    if raw.isdigit():
        sem_int = int(raw)
        if sem_int not in INT_TO_ROMAN:
            raise RollNumberError(f"Semester must be between 1 and 12 (got {raw}).")
        return sem_int, INT_TO_ROMAN[sem_int]

    if raw in ROMAN_TO_INT:
        sem_int = ROMAN_TO_INT[raw]
        return sem_int, raw

    raise RollNumberError(f"Invalid semester '{semester}'. Use 1-12 or Roman numerals I-XII.")


def normalize_roll_input(value: str | int) -> tuple[int, str]:
    """
    Validates and normalizes a numeric student roll number.
    - Accepts positive integers 1..999 (e.g., '01', '1', '43', '105').
    - Rejects 0, '00', negative numbers, empty strings, or non-digits.
    - Formats 1..99 with 2-digit zero-padding ('01'..'99') and >=100 as '100'.
    Returns: (roll_int, padded_roll_str)
    """
    if isinstance(value, bool) or value is None:
        raise RollNumberError("Roll number cannot be empty.")

    raw = str(value).strip()
    if not raw or not raw.isdigit():
        raise RollNumberError(f"Invalid roll number '{value}'. Enter digits only (e.g., 01, 15).")

    roll_int = int(raw)
    if roll_int <= 0 or roll_int > 999:
        raise RollNumberError(f"Roll number must be between 1 and 999 (got '{value}').")

    padded = f"{roll_int:02d}" if roll_int < 100 else str(roll_int)
    return roll_int, padded


def format_roll_number(major: str, semester: int | str, roll_number: int | str) -> str:
    """
    Constructs the canonical roll number:
    Sem-{ROMAN_SEMESTER}-{CANONICAL_MAJOR}-{ROLL_NUMBER}
    Example: Sem-III-CEIT-01
    """
    canon_major = normalize_major_strict(major).upper()
    _, roman_sem = normalize_semester_input(semester)
    _, padded_roll = normalize_roll_input(roll_number)
    return f"Sem-{roman_sem}-{canon_major}-{padded_roll}"


def parse_roll_number(value: str | int) -> dict:
    """
    Parses either a pure numeric roll input ('01') or a full/legacy roll string
    ('Sem-II-CEIT-01', 'Sem-2-CEIT-1', 'EC-05').
    Returns a dict:
      {
        "is_numeric_only": bool,
        "semester": int | None,
        "semester_roman": str | None,
        "major": str | None,         # lowercase canonical major
        "roll_number": int,
        "roll_str": str,             # '01'
        "canonical": str | None      # 'Sem-II-CEIT-01' if major & semester present
      }
    """
    if isinstance(value, bool) or value is None:
        raise RollNumberError("Roll number cannot be empty.")

    raw = str(value).strip()
    if not raw:
        raise RollNumberError("Roll number cannot be empty.")

    # Case 1: Pure numeric input (e.g., '01', '5', '43')
    if raw.isdigit():
        r_int, r_str = normalize_roll_input(raw)
        return {
            "is_numeric_only": True,
            "semester": None,
            "semester_roman": None,
            "major": None,
            "roll_number": r_int,
            "roll_str": r_str,
            "canonical": None
        }

    # Case 2: Standard Canonical format (Sem-III-CEIT-01)
    m_canon = CANONICAL_ROLL_RE.match(raw)
    if m_canon:
        sem_int, sem_roman = normalize_semester_input(m_canon.group(1))
        canon_maj = normalize_major_strict(m_canon.group(2))
        r_int, r_str = normalize_roll_input(m_canon.group(3))
        return {
            "is_numeric_only": False,
            "semester": sem_int,
            "semester_roman": sem_roman,
            "major": canon_maj,
            "roll_number": r_int,
            "roll_str": r_str,
            "canonical": f"Sem-{sem_roman}-{canon_maj.upper()}-{r_str}"
        }

    # Case 3: Legacy Arabic Semester format (Sem-2-CEIT-01)
    m_leg = LEGACY_ARABIC_ROLL_RE.match(raw)
    if m_leg:
        sem_int, sem_roman = normalize_semester_input(m_leg.group(1))
        canon_maj = normalize_major_strict(m_leg.group(2))
        r_int, r_str = normalize_roll_input(m_leg.group(3))
        return {
            "is_numeric_only": False,
            "semester": sem_int,
            "semester_roman": sem_roman,
            "major": canon_maj,
            "roll_number": r_int,
            "roll_str": r_str,
            "canonical": f"Sem-{sem_roman}-{canon_maj.upper()}-{r_str}"
        }

    # Case 4: Short Major-Roll format (e.g., CEIT-01, EC-5)
    m_short = SHORT_MAJOR_ROLL_RE.match(raw)
    if m_short:
        canon_maj = normalize_major_strict(m_short.group(1))
        r_int, r_str = normalize_roll_input(m_short.group(2))
        return {
            "is_numeric_only": False,
            "semester": None,
            "semester_roman": None,
            "major": canon_maj,
            "roll_number": r_int,
            "roll_str": r_str,
            "canonical": None
        }

    raise RollNumberError(
        f"Unrecognized roll number format '{value}'. "
        "Please enter only your roll number digits (e.g., 01) or full format (Sem-III-CEIT-01)."
    )


# ================= 🗄️ DYNAMIC SEMESTER CONFIGURATION =================
async def get_academic_setting(major: str) -> dict | None:
    """Return academic settings from the selected major database."""
    canon_major = normalize_major_strict(major)
    db = get_db(major=canon_major)
    if db is None:
        return None
    return await db.academic_settings.find_one({"major": canon_major})


async def get_current_semester(major: str, strict: bool = True) -> int | None:
    """
    Returns the configured active integer semester for the specified major.
    If not configured and strict=True, raises SemesterNotConfiguredError.
    Never silently defaults to semester II or III.
    """
    canon_major = normalize_major_strict(major)
    doc = await get_academic_setting(canon_major)
    if not doc or "current_semester" not in doc:
        if strict:
            raise SemesterNotConfiguredError(
                f"⚠️ [{canon_major.upper()}] မေဂျာအတွက် လက်ရှိ Semester သတ်မှတ်ထားခြင်း မရှိသေးပါ။\n"
                f"Admin မှ <code>/setsemester {canon_major} &lt;semester_no&gt;</code> ဖြင့် အရင်သတ်မှတ်ပေးရန် လိုအပ်ပါသည်။"
            )
        return None
    sem_int, _ = normalize_semester_input(doc["current_semester"])
    return sem_int


async def set_current_semester(
    major: str,
    semester: int | str,
    admin_id: int,
    academic_year: str | None = None
) -> dict:
    """
    Updates the active semester for a specific major in db.academic_settings
    and writes an immutable audit log entry in db.academic_audit_logs.
    Does NOT overwrite historical payment records.
    """
    canon_major = normalize_major_strict(major)
    db = get_db(major=canon_major)
    if db is None:
        raise RuntimeError("Database connection is not available.")

    sem_int, sem_roman = normalize_semester_input(semester)

    existing = await db.academic_settings.find_one({"major": canon_major})
    previous_semester = existing.get("current_semester") if existing else None
    previous_year = existing.get("academic_year") if existing else None
    new_year = academic_year or previous_year or "2026-2027"
    now_str = get_mm_now_str()

    await db.academic_settings.update_one(
        {"major": canon_major},
        {"$set": {
            "major": canon_major,
            "current_semester": sem_int,
            "semester_roman": sem_roman,
            "academic_year": new_year,
            "updated_by": admin_id,
            "updated_at": now_str
        }},
        upsert=True
    )

    audit_entry = {
        "action": "set_semester",
        "major": canon_major,
        "previous_semester": previous_semester,
        "new_semester": sem_int,
        "previous_academic_year": previous_year,
        "new_academic_year": new_year,
        "admin_id": admin_id,
        "timestamp": now_str
    }
    await db.academic_audit_logs.insert_one(audit_entry)
    return audit_entry


async def build_canonical_roll_for_major(major: str, roll_input: str | int) -> tuple[str, int, int, str]:
    """
    Given a verified major and a numeric roll input, fetches the major's active semester
    and constructs the canonical roll string.
    Returns: (canonical_roll, semester_int, roll_int, roll_padded_str)
    """
    canon_major = normalize_major_strict(major)
    sem_int = await get_current_semester(canon_major, strict=True)
    r_int, r_str = normalize_roll_input(roll_input)
    canonical = format_roll_number(canon_major, sem_int, r_int)
    return canonical, sem_int, r_int, r_str


async def get_current_roll_number(user_id: int, major: str | None = None) -> str | None:
    """
    Resolves a registered student's short roll number from db.student_info.
    """
    query: dict = {"$or": [{"tg_id": user_id}, {"user_id": user_id}]}
    if major:
        canon_major = normalize_major_strict(major)
        db = get_db(major=canon_major)
        if db is None:
            return None
        query["major"] = canon_major
        student = await db.student_info.find_one(query)
        return student.get("roll_no") if student else None

    if not is_major_database_enabled():
        db = get_db()
        if db is None:
            return None
        student = await db.student_info.find_one(query)
        return student.get("roll_no") if student else None

    matches = []
    for db_major, db in get_all_major_dbs():
        student = await db.student_info.find_one(query)
        if student:
            matches.append((db_major, student.get("roll_no")))
    unique_majors = {major_name for major_name, _ in matches}
    if len(unique_majors) != 1 or not matches:
        return None
    return matches[0][1]


# ================= 🔄 DELIBERATE SEMESTER ADVANCEMENT =================
async def advance_major_students_semester(
    major: str,
    target_semester: int | str,
    admin_id: int
) -> dict:
    """
    Deliberate, admin-triggered operation to advance existing students in a major
    to a new semester roll prefix while preserving their internal_id, roll_history,
    and historical records.
    """
    canon_major = normalize_major_strict(major)
    db = get_db(major=canon_major)
    if db is None:
        raise RuntimeError("Database connection is not available.")
    sem_int, sem_roman = normalize_semester_input(target_semester)
    now_str = get_mm_now_str()

    # 1. Update academic_settings first (logs audit event)
    await set_current_semester(canon_major, sem_int, admin_id)

    # 2. Keep roll_no short; semester is stored separately in current_semester.
    students = await db.student_info.find({"major": canon_major}).to_list(length=None)
    updated_count = 0

    for st in students:
        old_roll_no = st.get("roll_no", "")
        r_int = st.get("roll_number")
        if not r_int:
            try:
                parsed = parse_roll_number(old_roll_no)
                r_int = parsed["roll_number"]
            except RollNumberError:
                continue

        _, r_str = normalize_roll_input(r_int)
        old_sem = st.get("current_semester")
        old_short_roll = r_str
        try:
            old_short_roll = parse_roll_number(old_roll_no)["roll_str"] if old_roll_no else r_str
        except RollNumberError:
            pass

        update_doc: dict = {
            "$set": {
                "current_semester": sem_int,
                "roll_number": r_int,
                "roll_number_str": r_str,
                "roll_no": r_str,
                "updated_at": now_str
            },
            "$unset": {"student_id": ""}
        }
        if not st.get("internal_id"):
            update_doc["$set"]["internal_id"] = f"stu_{uuid.uuid4().hex[:12]}"

        if old_roll_no and (old_sem != sem_int or old_short_roll != r_str):
            update_doc["$push"] = {
                "roll_history": {
                    "semester": old_sem,
                    "roll_no": old_short_roll,
                    "roll_number": r_int,
                    "archived_at": now_str,
                    "changed_by": admin_id
                }
            }

        await db.student_info.update_one({"_id": st["_id"]}, update_doc)
        updated_count += 1

    return {
        "major": canon_major,
        "new_semester": sem_int,
        "semester_roman": sem_roman,
        "students_updated": updated_count
    }


# ================= 🛡️ SAFE LEGACY MIGRATION & RECONCILIATION =================
async def reconcile_legacy_student_records(dry_run: bool = True, major: str | None = None) -> dict:
    """
    Inspects all existing student_info records:
    - Identifies legacy roll formats (e.g., Sem-II-CEIT-01, Sem-2-CEIT-01).
    - Populates stable `internal_id`, `major`, `roll_number`, `roll_number_str`, and `legacy_roll_no`.
    - Sets `current_semester` from configured academic_settings (if available) or parsed roll semester.
    - Detects duplicate (major, roll_number) pairs and unresolved/malformed roll numbers.
    - Never overwrites `paid_funds` or `payment_history`.
    """
    if major:
        canon_scope = normalize_major_strict(major)
        db = get_db(major=canon_scope)
    else:
        if is_major_database_enabled():
            raise ValueError("When USE_MAJOR_DATABASES=true, specify a major: /reconcilerolls <major> [apply].")
        db = get_db()
    if db is None:
        raise RuntimeError("Database connection unavailable.")

    students = await db.student_info.find({}).to_list(length=None)
    settings_docs = await db.academic_settings.find({}).to_list(length=None)
    configured_semesters = {
        doc["major"]: doc["current_semester"]
        for doc in settings_docs
        if "major" in doc and "current_semester" in doc
    }

    seen_slots: dict[tuple[str, int], list[str]] = {}
    migrated = []
    unresolved = []
    duplicates = []

    for st in students:
        doc_id = str(st.get("_id"))
        raw_roll = str(st.get("roll_no") or "").strip()
        try:
            parsed = parse_roll_number(raw_roll)
        except RollNumberError as e:
            unresolved.append({"_id": doc_id, "roll_no": raw_roll, "name": st.get("name"), "reason": str(e)})
            continue

        major = st.get("major") or parsed.get("major")
        if not major and is_major_database_enabled() and canon_scope:
            major = canon_scope
        if not major:
            unresolved.append({
                "_id": doc_id,
                "roll_no": raw_roll,
                "name": st.get("name"),
                "reason": "Cannot determine major from roll_no or document."
            })
            continue

        try:
            canon_major = normalize_major_strict(major)
        except RollNumberError as e:
            unresolved.append({"_id": doc_id, "roll_no": raw_roll, "name": st.get("name"), "reason": str(e)})
            continue

        r_int = parsed["roll_number"]
        r_str = parsed["roll_str"]

        # Track duplicates within (major, roll_number)
        slot_key = (canon_major, r_int)
        seen_slots.setdefault(slot_key, []).append(doc_id)

        # Determine semester: prefer configured academic_settings, fallback to parsed semester
        sem_int = configured_semesters.get(canon_major) or parsed.get("semester")
        if not sem_int:
            unresolved.append({
                "_id": doc_id,
                "roll_no": raw_roll,
                "name": st.get("name"),
                "reason": f"No semester in roll_no and no academic_settings configured for {canon_major}."
            })
            continue

        short_roll = r_str
        internal_id = st.get("internal_id") or f"stu_{uuid.uuid4().hex[:12]}"

        changes = {
            "internal_id": internal_id,
            "major": canon_major,
            "current_semester": sem_int,
            "roll_number": r_int,
            "roll_number_str": r_str,
            "roll_no": short_roll,
        }
        if not st.get("legacy_roll_no"):
            changes["legacy_roll_no"] = raw_roll

        migrated.append({
            "_id": doc_id,
            "name": st.get("name"),
            "old_roll_no": raw_roll,
            "new_roll_no": short_roll,
            "major": canon_major,
            "semester": sem_int,
            "roll_number": r_int
        })

        if not dry_run:
            update_ops: dict = {"$set": changes, "$unset": {"student_id": ""}}
            if raw_roll != short_roll:
                update_ops["$addToSet"] = {
                    "roll_history": {
                        "semester": parsed.get("semester") or st.get("current_semester") or sem_int,
                        "roll_no": raw_roll,
                        "roll_number": r_int,
                        "note": "legacy_reconciliation"
                    }
                }
            await db.student_info.update_one({"_id": st["_id"]}, update_ops)

    for (maj, r_num), doc_ids in seen_slots.items():
        if len(doc_ids) > 1:
            duplicates.append({
                "major": maj,
                "roll_number": r_num,
                "document_ids": doc_ids
            })

    return {
        "dry_run": dry_run,
        "total_inspected": len(students),
        "ready_or_migrated_count": len(migrated),
        "unresolved_count": len(unresolved),
        "duplicate_slots_count": len(duplicates),
        "unresolved": unresolved,
        "duplicates": duplicates,
        "sample_migrations": migrated[:10]
    }