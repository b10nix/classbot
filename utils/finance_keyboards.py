"""Reusable inline keyboards for finance fund selectors and confirmations."""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

PAGE_SIZE = 8


def fund_picker_keyboard(token: str, options: list[dict], page: int = 0) -> InlineKeyboardMarkup:
    start = page * PAGE_SIZE
    selected = options[start:start + PAGE_SIZE]
    rows: list[list[InlineKeyboardButton]] = []
    for index, item in enumerate(selected, start=start):
        name = str(item.get("fund_name") or item.get("fund_id") or "Fund")
        fund_id = str(item.get("fund_id") or "")
        label = f"{name[:24]} · {fund_id[:15]}"
        rows.append([InlineKeyboardButton(label[:60], callback_data=f"finpick:select:{token}:{index}")])

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ အရင်", callback_data=f"finpick:page:{token}:{page - 1}"))
    if start + PAGE_SIZE < len(options):
        nav.append(InlineKeyboardButton("နောက် ➡️", callback_data=f"finpick:page:{token}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("✖️ မလုပ်တော့ပါ", callback_data=f"finpick:cancel:{token}")])
    return InlineKeyboardMarkup(rows)


def confirmation_keyboard(token: str, *, confirm_label: str = "✅ အတည်ပြုမည်") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(confirm_label, callback_data=f"finpick:confirm:{token}")],
        [InlineKeyboardButton("✖️ မလုပ်တော့ပါ", callback_data=f"finpick:cancel:{token}")],
    ])
