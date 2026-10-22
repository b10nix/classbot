"""MongoDB connection and safe, opt-in multi-major database routing.

Legacy mode is the default so current deployments keep using ClassBotDB until
its data has been reviewed and migrated.  Enable USE_MAJOR_DATABASES only after
the migration script has been run and verified.
"""
from __future__ import annotations

import logging
import os

import certifi
from motor.motor_asyncio import AsyncIOMotorClient

logger = logging.getLogger(__name__)

LEGACY_DB_NAME = os.getenv("MONGO_SHARED_DB_NAME", "ClassBotDB").strip() or "ClassBotDB"
DB_NAME = LEGACY_DB_NAME  # Backward compatibility for existing imports.
MAJOR_DB_PREFIX = os.getenv("MONGO_MAJOR_DB_PREFIX", "plumber").strip() or "plumber"
# Architecture decision: all majors share a single Mongo database.
# Kept as a compatibility flag for old imports; it is intentionally always false.
USE_MAJOR_DATABASES = False

# Keep this list independent of services to avoid import cycles.
VALID_MAJOR_CODES = ("ceit", "ep", "ec", "archi", "mc", "mech", "civil")
MAJOR_ALIASES = {
    "it": "ceit", "ceit": "ceit", "ep": "ep", "ec": "ec",
    "arch": "archi", "archi": "archi", "mc": "mc",
    "me": "mech", "mech": "mech", "c": "civil", "civil": "civil",
}

client: AsyncIOMotorClient | None = None
db = None  # Legacy/shared database handle; retained for tests and old callers.


def normalize_db_major(major: str) -> str:
    """Return a canonical major slug or raise ValueError for invalid input."""
    if not isinstance(major, str) or not major.strip():
        raise ValueError("A valid major code is required to select a database.")
    raw = major.strip().lower()
    canonical = MAJOR_ALIASES.get(raw)
    if canonical not in VALID_MAJOR_CODES:
        choices = ", ".join(VALID_MAJOR_CODES)
        raise ValueError(f"Invalid major '{major}'. Expected one of: {choices}.")
    return canonical


def is_major_database_enabled() -> bool:
    """Whether per-major database routing has been explicitly enabled."""
    return USE_MAJOR_DATABASES


def connect_is_ready() -> bool:
    return client is not None and db is not None


def get_shared_db():
    """Return the shared/legacy database (config, roles, social features)."""
    return db


def get_major_db(major: str):
    """Return the shared database; major-specific records use a `major` field."""
    # Validate the identifier to catch typos while keeping storage in one DB.
    normalize_db_major(major)
    return db


def get_all_major_dbs() -> list[tuple[str, object]]:
    """Legacy-compatible scan list; the project now uses one shared database."""
    return [("legacy", db)] if db is not None else []


def get_db(major: str | None = None):
    """Backward-compatible database accessor.

    Existing get_db() calls still return the shared/legacy database. Data
    handlers should pass major=... to route academic/finance data when the
    opt-in multi-major mode is enabled.
    """
    if major is None:
        return get_shared_db()
    return get_major_db(major)


async def connect_db() -> None:
    """Create the Mongo client and verify connectivity; raise on failure."""
    global client, db

    mongo_uri = os.getenv("MONGO_URI")
    if not mongo_uri:
        logger.error("MONGO_URI is missing; database connection was not created.")
        raise RuntimeError("MONGO_URI is required.")

    try:
        logger.info("Connecting to MongoDB Atlas...")
        new_client = AsyncIOMotorClient(
            mongo_uri,
            tls=True,
            tlsCAFile=certifi.where(),
            serverSelectionTimeoutMS=5000,
        )
        await new_client.admin.command("ping")
        client = new_client
        db = client[LEGACY_DB_NAME]
        logger.info("MongoDB connected. Shared/legacy database: %s", LEGACY_DB_NAME)
        if USE_MAJOR_DATABASES:
            logger.warning(
                "Per-major databases ENABLED (prefix=%s). Verify that data migration "
                "has completed before production use.",
                MAJOR_DB_PREFIX,
            )
        else:
            logger.info("Per-major database routing disabled; using legacy mode.")
    except Exception:
        if "new_client" in locals():
            new_client.close()
        client = None
        db = None
        logger.exception("MongoDB connection failed.")
        raise


async def close_db() -> None:
    """Close the MongoDB client cleanly on application shutdown."""
    global client, db
    if client is not None:
        client.close()
    client = None
    db = None
