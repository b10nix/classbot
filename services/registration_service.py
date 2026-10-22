"""Validation helpers for student self-registration."""
from __future__ import annotations

import re


_NAME_RE = re.compile(r"[A-Za-z][A-Za-z .'-]{0,78}[A-Za-z]")
_PHONE_LOCAL_RE = re.compile(r"0\d{7,10}")
_PHONE_INTL_RE = re.compile(r"\+?95\d{7,10}")


def normalize_student_name(value: str) -> str:
    """Validate an English name and normalize each word to title case."""
    name = " ".join(str(value or "").split())
    if len(name) < 2 or len(name) > 80 or not _NAME_RE.fullmatch(name):
        raise ValueError(
            "နာမည်ကို English စာလုံးများဖြင့်သာ ထည့်ပါ။ Space, apostrophe (')၊ "
            "hyphen (-) နဲ့ period (.) ကို လိုအပ်သလို သုံးနိုင်ပါတယ်။"
        )
    if not any(char.isalpha() for char in name):
        raise ValueError("English နာမည် မှန်ကန်စွာ ထည့်ပေးပါ။")
    return name.title()


def normalize_phone_number(value: str) -> str:
    """Accept a Myanmar local number (09...) or international (+959.../959...)."""
    compact = re.sub(r"[\s().-]", "", str(value or ""))
    if _PHONE_LOCAL_RE.fullmatch(compact) or _PHONE_INTL_RE.fullmatch(compact):
        return compact
    raise ValueError(
        "ဖုန်းနံပါတ် ပုံစံမမှန်ပါ။ 09xxxxxxxxx သို့မဟုတ် +959xxxxxxxxx ပုံစံဖြင့် ထည့်ပါ။"
    )
