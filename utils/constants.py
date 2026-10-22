# utils/constants.py

import os
from dotenv import load_dotenv

load_dotenv(override=True)

# 📌 Master & Group IDs
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
LOG_GROUP_ID = int(os.getenv("LOG_GROUP_ID", "0"))

# Major-specific Telegram groups. Each variable accepts one or more comma-separated IDs.
# Examples: CEIT_MAJOR=-1001234567890, EP_MAJOR=-1001234567891
MAJORS = ["ceit", "ep", "ec", "archi", "mc", "mech", "civil"]
MAJOR_ENV_KEYS = {
    "ceit": "CEIT_MAJOR",
    "ep": "EP_MAJOR",
    "ec": "EC_MAJOR",
    "archi": "ARCHI_MAJOR",
    "mc": "MC_MAJOR",
    "mech": "MECH_MAJOR",
    "civil": "CIVIL_MAJOR",
}

def _parse_chat_ids(raw: str | None, *, env_name: str) -> list[int]:
    ids: list[int] = []
    for token in (raw or "").split(","):
        token = token.strip()
        if not token or token == "0":
            continue
        try:
            ids.append(int(token))
        except ValueError:
            import logging
            logging.getLogger(__name__).error("Invalid Telegram chat ID in %s: %r", env_name, token)
    return list(dict.fromkeys(ids))

# Shared groups configured by CLASS_GROUP_ID are reserved for social posts/confessions
# and weather alerts. Parse them before Major groups so accidental overlaps can be rejected.
CLASS_GROUP_IDS = _parse_chat_ids(os.getenv("CLASS_GROUP_ID", ""), env_name="CLASS_GROUP_ID")
_SHARED_GROUP_ID_SET = set(CLASS_GROUP_IDS)

MAJOR_GROUP_IDS: dict[str, list[int]] = {}
for _major, _env_key in MAJOR_ENV_KEYS.items():
    _raw_major_ids = _parse_chat_ids(os.getenv(_env_key, ""), env_name=_env_key)
    _overlap_ids = _SHARED_GROUP_ID_SET.intersection(_raw_major_ids)
    if _overlap_ids:
        import logging
        logging.getLogger(__name__).error(
            "%s contains shared CLASS_GROUP_ID value(s); ignoring overlapping IDs for academic delivery.",
            _env_key,
        )
    MAJOR_GROUP_IDS[_major] = [gid for gid in _raw_major_ids if gid not in _SHARED_GROUP_ID_SET]

# Reverse lookup for private Major groups. Used for Major detection only.
GROUP_DEFAULT_MAJORS: dict[int, set[str]] = {}
for _major, _ids in MAJOR_GROUP_IDS.items():
    for _gid in _ids:
        GROUP_DEFAULT_MAJORS.setdefault(_gid, set()).add(_major)

def get_default_majors_for_group(group_id: int) -> set[str]:
    """Configured Major(s) for a Major-specific group, or an empty set."""
    return set(GROUP_DEFAULT_MAJORS.get(int(group_id), set()))

def get_major_group_ids() -> list[int]:
    """Return only environment-configured Major-specific group IDs, deduplicated."""
    return list(dict.fromkeys(gid for ids in MAJOR_GROUP_IDS.values() for gid in ids))

# 📌 System-wide Text Constants
FOOTER = "\n\n<i>• Omni-Net</i>"

# 📌 Academic Constants (Single Source of Truth)
DAYS_OF_WEEK = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
SUBJECTS_LIST = ["Myanmar", "English", "Maths", "Physics", "CEIT", "Workshop"]

# 📌 Random Emoji List
RANDOM_EMOJIS = [
    "🦊", "🐼", "🐯", "🦁", "🐮", "🐷", "🐸", "🐵", "🐔", "🐧", "🦉", "🦄", "🐝", "🦋", "🐙", "🐢", "🐍", "🐳", "🐉", "🌵",
    "🌻", "🍎", "🍉", "🍓", "🍕", "🍔", "🍩", "⚽", "🎸", "🚗", "🚀", "💎", "🔮", "🎭", "🎨", "🤡", "👻", "👽", "👾", "🤖",
    "🐶", "🐱", "🐭", "🐹", "🐰", "🐻", "🐨", "🐒", "🦍", "🦧", "🦮", "🐕", "🐩", "🐺", "🦝", "🐗", "🐴", "🦓", "🦌", "🦬",
    "🦏", "🦛", "🐘", "🦣", "🐪", "🐫", "🦒", "🦘", "🦥", "🦦", "🦨", "🦡", "🐾", "🦃", "🕊️", "🦅", "🦆", "🦢", "🦜", "🦩",
    "🦚", "🐕‍🦺", "🐈", "🐈‍⬛", "🐊", "🦖", "🦕", "🐡", "🐠", "🐟", "🐬", "🦈", "🦭", "🦀", "🦞", "🦐", "🦑", "🐌", "🐛", "🐜",
    "🐞", "🦗", "🕷️", "🦂", "🦟", "🦠", "🏔️", "🌋", "🏕️", "🏖️", "🏜️", "🏝️", "🏞️", "🪐", "🌟", "🔥", "💧", "🌊", "🌈", "⛄"
]


def _get_vip_users() -> list[int]:
    vip_str = os.getenv("VIP_USERS", "")
    try:
        return [int(u.strip()) for u in vip_str.split(",") if u.strip()]
    except ValueError:
        return []

VIP_USERS = _get_vip_users()