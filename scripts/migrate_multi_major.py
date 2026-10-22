"""Copy major-scoped records from the legacy MongoDB into per-major databases.

Default operation is a dry run. Nothing is written unless --apply is supplied.
Source records are never deleted or modified. Ambiguous records are reported and
left in the source DB; use --legacy-major only if *all* otherwise-unidentified
major-scoped records in the legacy database truly belong to that one major.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
from collections import Counter
from copy import deepcopy
from typing import Any

import certifi
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

MAJORS = ("ceit", "ep", "ec", "archi", "mc", "mech", "civil")
ALIASES = {
    "it": "ceit", "ceit": "ceit", "ep": "ep", "ec": "ec",
    "arch": "archi", "archi": "archi", "mc": "mc",
    "me": "mech", "mech": "mech", "c": "civil", "civil": "civil",
}
# These collections contain academic or finance records and should be tenant-scoped.
TENANT_COLLECTIONS = {
    "student_info", "academic_settings", "academic_audit_logs", "funds", "ec_ledgers",
    "fund_transactions", "transfers", "attendance_records", "tasks",
    "timetable", "sys_config",
}
ARCHIVE_PREFIXES = (
    "archive_fund_transactions_", "archive_funds_", "archive_transfers_",
    "archive_student_payments_", "archive_ec_ledgers_",
)
ROLL_MAJOR_RE = re.compile(r"(?:^|-)\s*(CEIT|IT|EP|EC|ARCHI|ARCH|MC|MECH|ME|CIVIL|C)\s*-", re.I)


def normalize_major(value: Any) -> str | None:
    if value is None:
        return None
    return ALIASES.get(str(value).strip().lower())


def infer_major_from_roll(value: Any) -> str | None:
    if not value:
        return None
    match = ROLL_MAJOR_RE.search(str(value).strip())
    return normalize_major(match.group(1)) if match else None


def infer_major_from_archive_name(collection_name: str) -> str | None:
    # Names look like archive_funds_ceit_sem2 or archive_ec_ledgers_archi_2026.
    # Pick the rightmost major-like token: the prefix `archive_ec_ledgers_`
    # itself contains an `_ec_` token and must not be mistaken for the major.
    lower = f"_{collection_name.lower()}_"
    candidates = []
    for alias, canonical in ALIASES.items():
        token = f"_{alias}_"
        pos = lower.rfind(token)
        if pos >= 0:
            candidates.append((pos, canonical))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def route_record(collection_name: str, doc: dict[str, Any], legacy_major: str | None) -> tuple[list[str], dict[str, Any], str | None]:
    """Return destination majors, cloned doc, and unresolved reason if any."""
    cloned = deepcopy(doc)
    raw_major = cloned.get("major")
    if isinstance(raw_major, str) and raw_major.strip().lower() == "all" and collection_name == "timetable":
        # A shared timetable is deliberately replicated into each tenant.
        return list(MAJORS), cloned, None

    major = normalize_major(raw_major)
    if not major and collection_name == "student_info":
        major = infer_major_from_roll(cloned.get("roll_no") or cloned.get("roll_number_str"))
    if not major and collection_name.startswith(ARCHIVE_PREFIXES):
        major = infer_major_from_archive_name(collection_name)
    if not major and legacy_major:
        major = legacy_major
    if not major:
        return [], cloned, "major missing or unrecognized; left in legacy source"

    cloned["major"] = major
    return [major], cloned, None


async def run(args: argparse.Namespace) -> int:
    load_dotenv(override=False)
    uri = os.getenv("MONGO_URI")
    if not uri:
        print("ERROR: MONGO_URI is not set. Put it in .env or the environment.")
        return 2

    source_name = args.source_db or os.getenv("MONGO_SHARED_DB_NAME", "ClassBotDB")
    prefix = args.target_prefix or os.getenv("MONGO_MAJOR_DB_PREFIX", "plumber")
    legacy_major = normalize_major(args.legacy_major) if args.legacy_major else None
    if args.legacy_major and not legacy_major:
        print(f"ERROR: invalid --legacy-major {args.legacy_major!r}.")
        return 2

    client = AsyncIOMotorClient(uri, tls=True, tlsCAFile=certifi.where(), serverSelectionTimeoutMS=10000)
    try:
        await client.admin.command("ping")
        source = client[source_name]
        existing = set(await source.list_collection_names())
        collections = sorted((TENANT_COLLECTIONS & existing) | {
            name for name in existing if name.startswith(ARCHIVE_PREFIXES)
        })
        if not collections:
            print(f"No tenant collections found in source database {source_name!r}.")
            return 0

        counts: Counter[str] = Counter()
        unresolved_samples: list[tuple[str, str, str]] = []
        collisions: list[tuple[str, str, str]] = []

        print(f"Mode: {'APPLY (copy only; no delete/update in source)' if args.apply else 'DRY RUN (no writes)'}")
        print(f"Source database: {source_name}")
        print(f"Destination databases: {prefix}_<major> for {', '.join(MAJORS)}")
        if legacy_major:
            print(f"Legacy fallback explicitly enabled for: {legacy_major.upper()}")
        print("-" * 72)

        for collection_name in collections:
            source_collection = source[collection_name]
            total = 0
            async for source_doc in source_collection.find({}):
                total += 1
                destinations, cloned, reason = route_record(collection_name, source_doc, legacy_major)
                if reason:
                    counts["unresolved"] += 1
                    if len(unresolved_samples) < 30:
                        unresolved_samples.append((collection_name, str(source_doc.get("_id")), reason))
                    continue

                for major in destinations:
                    target_name = f"{prefix}_{major}"
                    target_collection = client[target_name][collection_name]
                    found = await target_collection.find_one({"_id": cloned["_id"]}, {"_id": 1})
                    if found:
                        counts["already_exists"] += 1
                        # Same _id with different content is a potential migration conflict.
                        existing_doc = await target_collection.find_one({"_id": cloned["_id"]})
                        compare_source = deepcopy(cloned)
                        compare_existing = deepcopy(existing_doc or {})
                        compare_source.pop("_id", None)
                        compare_existing.pop("_id", None)
                        if compare_source != compare_existing and len(collisions) < 30:
                            collisions.append((target_name, collection_name, str(cloned["_id"])))
                        continue
                    counts["would_copy" if not args.apply else "copied"] += 1
                    if args.apply:
                        await target_collection.insert_one(cloned)

            print(f"{collection_name}: source documents={total}")

        print("-" * 72)
        for key in ("would_copy", "copied", "already_exists", "unresolved"):
            if counts[key]:
                print(f"{key}: {counts[key]}")
        if unresolved_samples:
            print("\nUnresolved sample (source is untouched):")
            for coll, doc_id, why in unresolved_samples:
                print(f"  - {coll} _id={doc_id}: {why}")
        if collisions:
            print("\nExisting destination records whose contents differ (NOT overwritten):")
            for db_name, coll, doc_id in collisions:
                print(f"  - {db_name}.{coll} _id={doc_id}")

        if not args.apply:
            print("\nDry run complete. No records were written.")
        else:
            print("\nCopy complete. Source records were not deleted or modified.")
            if counts["unresolved"] or collisions:
                print("WARNING: resolve the unresolved records/conflicts before enabling multi-major routing.")
        if counts["unresolved"] or collisions:
            return 1
        return 0
    finally:
        client.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Actually copy data. Default is a read-only dry run.")
    parser.add_argument("--source-db", help="Override legacy source database name (default: MONGO_SHARED_DB_NAME/ClassBotDB).")
    parser.add_argument("--target-prefix", help="Override target database prefix (default: MONGO_MAJOR_DB_PREFIX/plumber).")
    parser.add_argument("--legacy-major", help="Explicitly assign otherwise ambiguous tenant records to one major. Only use if all such records belong to it.")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))
