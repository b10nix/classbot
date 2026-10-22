"""Inline keyboards for the /nuke permanent Fund deletion workflow."""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def fund_picker_keyboard(funds: list[dict], page: int, pages: int, requester_id: int, page_size: int = 6) -> InlineKeyboardMarkup:
    """List actual Fund documents, including active, archived, completed, and cancelled funds."""
    rows = []
    start = page * page_size
    for fund in funds[start:start + page_size]:
        document_id = str(fund.get("_id") or "")
        if not document_id:
            continue
        status = str(fund.get("status") or "active").upper()
        name = str(fund.get("fund_name") or fund.get("title") or fund.get("fund_id") or "Unnamed fund")
        amount = int(fund.get("target_amount") or 0)
        # Stored Mongo _id values are normally 24-char ObjectIds; callback stays below Telegram's 64-byte cap.
        callback = f"nuke:pick:{document_id}:{int(requester_id)}"
        if len(callback.encode("utf-8")) <= 64:
            rows.append([InlineKeyboardButton(f"{status} · {name[:23]} · {amount:,}", callback_data=callback)])

    navigation = []
    if page > 0:
        navigation.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"nuke:list:{page - 1}:{int(requester_id)}"))
    if page + 1 < pages:
        navigation.append(InlineKeyboardButton("Next ➡️", callback_data=f"nuke:list:{page + 1}:{int(requester_id)}"))
    if navigation:
        rows.append(navigation)
    rows.append([InlineKeyboardButton("✖️ မလုပ်တော့ပါ", callback_data=f"nuke:stop:{int(requester_id)}")])
    return InlineKeyboardMarkup(rows)


def draft_keyboard(request_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📨 ဒုတိယ EC ထံ Approval တောင်းမည်", callback_data=f"nuke:submit:{request_id}")],
        [InlineKeyboardButton("✖️ Request ကို ဖျက်သိမ်းမည်", callback_data=f"nuke:cancel:{request_id}")],
    ])


def pending_keyboard(request_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✖️ Approval Request ကို ပယ်ဖျက်ရန်", callback_data=f"nuke:cancel:{request_id}")
    ]])


def approver_keyboard(request_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve & Permanently Delete", callback_data=f"nuke:approve:{request_id}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"nuke:reject:{request_id}"),
    ]])
