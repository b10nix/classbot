"""Inline finance fund selection for archive/report/reminder/export/payment undo.

The selected IDs are stored in per-user bot context under a short random token,
not copied into callback_data. All callbacks re-check major authorization and
expire after 15 minutes.
"""
from __future__ import annotations

import html
import logging
import secrets
import time
from types import SimpleNamespace
from typing import Any

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from database.db import get_db
from services.finance_service import find_student_by_roll_in_major
from services.fund_selection_service import (
    build_undo_options,
    find_fund_option,
    list_fund_options,
    reminder_preview,
)
from services.major_service import get_user_major, is_major_authorized, normalize_major
from services.roll_number_service import CrossMajorAccessError, RollNumberError
from utils.auth import is_super_admin
from utils.finance_keyboards import confirmation_keyboard, fund_picker_keyboard

logger = logging.getLogger(__name__)
_STATE_KEY = "__plumber_finance_fund_pickers_v1"
_STATE_TTL_SECONDS = 15 * 60


class _CallbackMessageProxy:
    """Make legacy message-based finance handlers work from an inline callback."""
    def __init__(self, query):
        self._query = query
        self._message = query.message

    def __getattr__(self, name):
        return getattr(self._message, name)

    async def reply_text(self, text, **kwargs):
        try:
            return await self._query.edit_message_text(text=text, **kwargs)
        except Exception:
            # Some exports or long-running reminders may make the original
            # message non-editable. Fall back to a fresh message rather than fail.
            return await self._message.reply_text(text=text, **kwargs)

    async def reply_document(self, **kwargs):
        try:
            await self._query.edit_message_text("✅ Excel ဖိုင်ထုတ်ပြီးပါပြီ။ ဖိုင်ကို အောက်မှာ ပို့ထားပါတယ်။")
        except Exception:
            pass
        return await self._message.reply_document(**kwargs)


class _CallbackContextProxy:
    def __init__(self, context, args):
        self._context = context
        self.args = list(args)

    def __getattr__(self, name):
        return getattr(self._context, name)


def _states(context: ContextTypes.DEFAULT_TYPE) -> dict[str, dict[str, Any]]:
    states = context.user_data.get(_STATE_KEY)
    if not isinstance(states, dict):
        states = {}
        context.user_data[_STATE_KEY] = states
    now = time.time()
    for token in list(states):
        if float(states[token].get("expires_at", 0)) <= now:
            states.pop(token, None)
    return states


def _safe_name(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


async def show_fund_picker(
    target,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    user_id: int,
    major: str,
    action: str,
    title: str,
    options: list[dict[str, Any]] | None = None,
    extra: dict[str, Any] | None = None,
    edit: bool = False,
):
    canonical = normalize_major(major)
    if not canonical or not await is_major_authorized(int(user_id), canonical):
        text = "⛔ ဒီ Major အတွက် အသုံးပြုခွင့် မရှိပါ။"
        return await (target.edit_message_text(text) if edit else target.reply_text(text))

    if options is None:
        options = await list_fund_options(canonical, action)
    if not options:
        text = "📭 ရွေးချယ်စရာ ရန်ပုံငွေ မရှိသေးပါ။"
        return await (target.edit_message_text(text) if edit else target.reply_text(text))

    token = secrets.token_hex(5)
    state = {
        "token": token,
        "actor_id": int(user_id),
        "major": canonical,
        "action": action,
        "title": title,
        "options": options,
        "created_at": time.time(),
        "expires_at": time.time() + _STATE_TTL_SECONDS,
        **(extra or {}),
    }
    _states(context)[token] = state
    page_count = (len(options) + 7) // 8
    body = f"<b>[{_safe_name(canonical.upper())}] {_safe_name(title)}</b>\n\nအောက်က စာရင်းထဲက တစ်ခုကို ရွေးပါ။\nစာမျက်နှာ: 1/{page_count}"
    markup = fund_picker_keyboard(token, options, page=0)
    if edit:
        return await target.edit_message_text(body, reply_markup=markup, parse_mode=ParseMode.HTML)
    return await target.reply_text(body, reply_markup=markup, parse_mode=ParseMode.HTML)


def _state_for_callback(context, token: str, user_id: int) -> dict[str, Any] | None:
    state = _states(context).get(token)
    if not state or int(state.get("actor_id", 0)) != int(user_id):
        return None
    return state


async def _invoke_legacy(action: str, query, context, args: list[str]):
    """Reuse existing finance report/archive implementations after selection.

    The legacy handlers remain the single source for their business logic; this
    small adapter only supplies the selected Fund ID and maps their reply onto
    the inline-picker message.
    """
    if action in {"archive", "unarchive", "fundstat"}:
        from handlers.finance.finance_tools import archive_fund_cmd, unarchive_fund_cmd, fundstat_cmd
        handler = {"archive": archive_fund_cmd, "unarchive": unarchive_fund_cmd, "fundstat": fundstat_cmd}[action]
    elif action in {"report", "remind", "exportfund"}:
        from handlers.finance.reports import report_cmd, remind_cmd, export_fund_cmd
        handler = {"report": report_cmd, "remind": remind_cmd, "exportfund": export_fund_cmd}[action]
    elif action == "undo":
        from handlers.finance.fund_admin import undo_cmd
        handler = undo_cmd
    else:
        return await query.edit_message_text("⚠️ မသိသော finance action ဖြစ်ပါတယ်။")

    proxy_update = SimpleNamespace(
        message=_CallbackMessageProxy(query),
        effective_user=query.from_user,
        effective_chat=query.message.chat,
        callback_query=query,
    )
    proxy_context = _CallbackContextProxy(context, args)
    return await handler(proxy_update, proxy_context)


async def _show_reminder_confirmation(query, context, state: dict[str, Any], option: dict[str, Any]):
    preview = await reminder_preview(state["major"], option["fund_id"])
    if not preview:
        return await query.edit_message_text("⚠️ ဒီရန်ပုံငွေကို မတွေ့တော့ပါ။ /remind ကို ပြန်စပါ။")
    state["selected_index"] = int(state["options"].index(option))
    state["reminder_preview"] = {
        key: preview[key]
        for key in ("fund_id", "fund_name", "target_amount", "students_scanned", "needs_payment", "can_receive", "no_telegram_id")
    }
    state["expires_at"] = time.time() + _STATE_TTL_SECONDS
    text = (
        f"<b>သတိပေးစာ ပို့မယ့်အကြောင်း အတည်ပြုပါ</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Major: <b>{_safe_name(state['major'].upper())}</b>\n"
        f"ရန်ပုံငွေ: <b>{_safe_name(preview['fund_name'])}</b>\n"
        f"တစ်ဦးချင်းပေးရမည့်ငွေ: <b>{preview['target_amount']:,} MMK</b>\n\n"
        f"ငွေမပြည့်သေးသူ: <b>{preview['needs_payment']}</b> ဦး\n"
        f"စာပို့နိုင်မည့်သူ: <b>{preview['can_receive']}</b> ဦး\n"
        f"Telegram ID မရှိသေးသူ: <b>{preview['no_telegram_id']}</b> ဦး\n\n"
        "အတည်ပြုပြီးမှသာ reminder ပို့ပါမယ်။ Bot က ပို့မရသူတွေကို နောက်ဆုံးစာရင်းမှာ ပြပါမယ်။"
    )
    return await query.edit_message_text(
        text,
        reply_markup=confirmation_keyboard(state["token"], confirm_label="📩 သတိပေးစာ ပို့မည်"),
        parse_mode=ParseMode.HTML,
    )


async def _show_undo_confirmation(query, context, state: dict[str, Any], option: dict[str, Any]):
    state["selected_index"] = int(state["options"].index(option))
    state["expires_at"] = time.time() + _STATE_TTL_SECONDS
    text = (
        "<b>Payment ပြန်ရုပ်သိမ်းရန် အတည်ပြုပါ</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"Major: <b>{_safe_name(state['major'].upper())}</b>\n"
        f"ကျောင်းသား: <b>{_safe_name(state.get('student_name', 'Unknown'))}</b> "
        f"(<code>{_safe_name(state.get('roll_display', state.get('roll_query', '')))}</code>)\n"
        f"Fund: <b>{_safe_name(option['fund_name'])}</b>\n"
        f"နောက်ဆုံးသွင်းငွေ: <b>{int(option.get('last_amount', 0)):,} MMK</b>\n"
        f"သွင်းသည့်ရက်: <code>{_safe_name(option.get('last_date', 'Unknown'))}</code>\n"
        f"လက်ရှိမှတ်တမ်းပမာဏ: <b>{int(option.get('current_paid', 0)):,} MMK</b>\n\n"
        "အတည်ပြုလျှင် ဒီ Fund အတွက် နောက်ဆုံး payment မှတ်တမ်းကို ပြန်ရုပ်သိမ်းပါမယ်။"
    )
    return await query.edit_message_text(
        text,
        reply_markup=confirmation_keyboard(state["token"], confirm_label="⏪ Undo လုပ်မည်"),
        parse_mode=ParseMode.HTML,
    )


async def _find_student_for_undo(major: str, roll_query: str, user_id: int):
    return await find_student_by_roll_in_major(
        roll_query, major, allow_cross_major=is_super_admin(user_id)
    )


async def undo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return
    user_id = int(update.effective_user.id)
    major = normalize_major(await get_user_major(user_id))
    if not major or not await is_major_authorized(user_id, major):
        return await update.message.reply_text("⛔ သက်ဆိုင်ရာ Major အတွက် ခွင့်ပြုချက် မရှိပါ။")
    if not context.args:
        return await update.message.reply_text(
            "အသုံးပြုနည်း: <code>/undo &lt;RollNo&gt;</code>\nဥပမာ: <code>/undo 01</code>\n"
            "ကျောင်းသားကို ရွေးပြီးနောက် ပြန်ရုပ်သိမ်းမယ့် Fund ကိုရွေးကာ အတည်ပြုနိုင်ပါတယ်။",
            parse_mode=ParseMode.HTML,
        )

    roll_query = str(context.args[0])
    try:
        student = await _find_student_for_undo(major, roll_query, user_id)
    except (RollNumberError, CrossMajorAccessError) as exc:
        return await update.message.reply_text(f"⚠️ {_safe_name(exc)}", parse_mode=ParseMode.HTML)
    if not student:
        return await update.message.reply_text(
            f"⚠️ [{_safe_name(major.upper())}] မှာ Roll <code>{_safe_name(roll_query)}</code> ကျောင်းသားကို မတွေ့ပါ။",
            parse_mode=ParseMode.HTML,
        )

    all_funds = await list_fund_options(major, "report")
    names = {str(item["fund_id"]).casefold(): str(item["fund_name"]) for item in all_funds}
    options = build_undo_options(student, names)
    if len(context.args) > 1:
        requested = str(context.args[1]).casefold()
        options = [item for item in options if str(item["fund_id"]).casefold() == requested]
    if not options:
        return await update.message.reply_text(
            f"📭 <b>{_safe_name(student.get('name', 'ကျောင်းသား'))}</b> အတွက် ပြန်ရုပ်သိမ်းနိုင်တဲ့ payment မှတ်တမ်း မတွေ့ပါ။",
            parse_mode=ParseMode.HTML,
        )

    return await show_fund_picker(
        update.message,
        context,
        user_id=user_id,
        major=major,
        action="undo",
        title=f"{student.get('name', 'ကျောင်းသား')} ရဲ့ ပြန်ရုပ်သိမ်းမည့် payment ကို ရွေးပါ",
        options=options,
        extra={
            "roll_query": roll_query,
            "roll_display": str(student.get("roll_no") or roll_query),
            "student_name": str(student.get("name") or "Unknown"),
            "student_major": major,
        },
    )


async def archive_fund_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = int(update.effective_user.id)
    major = normalize_major(await get_user_major(user_id))
    if not major or not await is_major_authorized(user_id, major):
        return
    if context.args:
        from handlers.finance.finance_tools import archive_fund_cmd as legacy
        return await legacy(update, context)
    return await show_fund_picker(update.message, context, user_id=user_id, major=major,
                                  action="archive", title="Archive လုပ်မည့် ရန်ပုံငွေကို ရွေးပါ")


async def unarchive_fund_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = int(update.effective_user.id)
    major = normalize_major(await get_user_major(user_id))
    if not major or not await is_major_authorized(user_id, major):
        return
    if context.args:
        from handlers.finance.finance_tools import unarchive_fund_cmd as legacy
        return await legacy(update, context)
    return await show_fund_picker(update.message, context, user_id=user_id, major=major,
                                  action="unarchive", title="ပြန်ဖွင့်မည့် ရန်ပုံငွေကို ရွေးပါ")


async def report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = int(update.effective_user.id)
    major = normalize_major(await get_user_major(user_id))
    if not major or not await is_major_authorized(user_id, major):
        return
    if context.args:
        from handlers.finance.reports import report_cmd as legacy
        return await legacy(update, context)
    return await show_fund_picker(update.message, context, user_id=user_id, major=major,
                                  action="report", title="စာရင်းကြည့်မည့် ရန်ပုံငွေကို ရွေးပါ")


async def fundstat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = int(update.effective_user.id)
    major = normalize_major(await get_user_major(user_id))
    if not major or not await is_major_authorized(user_id, major):
        return
    if context.args:
        from handlers.finance.finance_tools import fundstat_cmd as legacy
        return await legacy(update, context)
    return await show_fund_picker(update.message, context, user_id=user_id, major=major,
                                  action="fundstat", title="ငွေပေးသွင်းမှု အခြေအနေကြည့်ရန် ရန်ပုံငွေရွေးပါ")


async def export_fund_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = int(update.effective_user.id)
    major = normalize_major(await get_user_major(user_id))
    if not major or not await is_major_authorized(user_id, major):
        return
    if context.args:
        from handlers.finance.reports import export_fund_cmd as legacy
        return await legacy(update, context)
    return await show_fund_picker(update.message, context, user_id=user_id, major=major,
                                  action="exportfund", title="Excel ဖိုင်ထုတ်မည့် ရန်ပုံငွေကို ရွေးပါ")


async def remind_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = int(update.effective_user.id)
    major = normalize_major(await get_user_major(user_id))
    if not major or not await is_major_authorized(user_id, major):
        return
    options = await list_fund_options(major, "remind")
    if context.args:
        wanted = str(context.args[0]).casefold()
        options = [item for item in options if item["fund_id"].casefold() == wanted]
    if context.args and not options:
        return await update.message.reply_text("ဒီ Fund ID ကို မတွေ့ပါ။ /remind ကို Fund မရွေးဘဲ ပြန်စပါ။")
    return await show_fund_picker(
        update.message, context, user_id=user_id, major=major,
        action="remind", title="သတိပေးစာပို့မည့် ရန်ပုံငွေကို ရွေးပါ", options=options,
    )


async def fund_selection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.from_user or not query.data:
        return
    parts = query.data.split(":")
    if len(parts) < 3 or parts[0] != "finpick":
        return await query.answer("Finance picker data မမှန်ပါ။", show_alert=True)
    action = parts[1]
    token = parts[2]
    state = _state_for_callback(context, token, int(query.from_user.id))
    if not state:
        return await query.answer("ဒီရွေးချယ်မှု သက်တမ်းကုန်သွားပါပြီ။ Command ကို ပြန်စပါ။", show_alert=True)
    if not await is_major_authorized(int(query.from_user.id), state["major"]):
        _states(context).pop(token, None)
        return await query.answer("ဒီ Major အတွက် ခွင့်ပြုချက် မရှိတော့ပါ။", show_alert=True)

    db = get_db(major=state["major"])
    if db is None:
        return await query.answer("Database ချိတ်ဆက်မရပါ။", show_alert=True)

    if action == "cancel":
        _states(context).pop(token, None)
        await query.answer()
        return await query.edit_message_text("✅ လုပ်ဆောင်ချက်ကို ရပ်လိုက်ပါပြီ။ Data မပြောင်းလဲပါ။")

    if action == "page":
        if len(parts) != 4:
            return await query.answer("စာမျက်နှာနံပါတ် မမှန်ပါ။", show_alert=True)
        try:
            page = int(parts[3])
        except ValueError:
            return await query.answer("စာမျက်နှာနံပါတ် မမှန်ပါ။", show_alert=True)
        if page < 0 or page * 8 >= len(state["options"]):
            return await query.answer("ဒီစာမျက်နှာ မရှိပါ။", show_alert=True)
        await query.answer()
        page_count = (len(state["options"]) + 7) // 8
        text = f"<b>[{_safe_name(state['major'].upper())}] {_safe_name(state.get('title', 'ရန်ပုံငွေရွေးပါ'))}</b>\n\nအောက်က စာရင်းထဲက တစ်ခုကို ရွေးပါ။\nစာမျက်နှာ: {page + 1}/{page_count}"
        return await query.edit_message_text(
            text, reply_markup=fund_picker_keyboard(token, state["options"], page), parse_mode=ParseMode.HTML
        )

    if action == "select":
        if len(parts) != 4:
            return await query.answer("ရွေးချယ်မှု မမှန်ပါ။", show_alert=True)
        try:
            index = int(parts[3])
            option = state["options"][index]
        except (ValueError, IndexError, TypeError):
            return await query.answer("ဒီရန်ပုံငွေရွေးချယ်မှု မရှိတော့ပါ။", show_alert=True)

        if state["action"] == "undo":
            await query.answer()
            return await _show_undo_confirmation(query, context, state, option)
        if state["action"] == "remind":
            await query.answer()
            return await _show_reminder_confirmation(query, context, state, option)

        # Re-resolve selected fund from the database before mutating or exporting.
        fresh = await find_fund_option(state["major"], str(option.get("fund_id")), state["action"])
        if not fresh:
            return await query.answer("ဒီရန်ပုံငွေ အခြေအနေပြောင်းသွားပါပြီ။ Command ကို ပြန်စပါ။", show_alert=True)
        args = [str(fresh["fund_id"])]
        await query.answer()
        _states(context).pop(token, None)
        return await _invoke_legacy(state["action"], query, context, args)

    if action == "confirm":
        if len(parts) != 3:
            return await query.answer("အတည်ပြုမှု မမှန်ပါ။", show_alert=True)
        try:
            selected = state["options"][int(state["selected_index"])]
        except (KeyError, ValueError, IndexError, TypeError):
            return await query.answer("ရွေးချယ်ထားတဲ့အချက်အလက် မရှိတော့ပါ။ ပြန်စပါ။", show_alert=True)

        if state["action"] == "remind":
            fresh_preview = await reminder_preview(state["major"], selected["fund_id"])
            old_preview = state.get("reminder_preview", {})
            if not fresh_preview:
                _states(context).pop(token, None)
                return await query.answer("ဒီရန်ပုံငွေကို မတွေ့တော့ပါ။", show_alert=True)
            keys = ("needs_payment", "can_receive", "no_telegram_id", "target_amount", "fund_id")
            if any(fresh_preview.get(key) != old_preview.get(key) for key in keys):
                await query.answer("စာရင်းပြောင်းသွားပါတယ်။ အချက်အလက်အသစ်ကို ပြန်စစ်ပါ။", show_alert=True)
                return await _show_reminder_confirmation(query, context, state, selected)
            await query.answer("သတိပေးစာ ပို့နေပါသည်...")
            _states(context).pop(token, None)
            return await _invoke_legacy("remind", query, context, [str(selected["fund_id"])])

        if state["action"] == "undo":
            roll_query = str(state.get("roll_query") or "")
            try:
                student = await _find_student_for_undo(state["major"], roll_query, int(query.from_user.id))
            except (RollNumberError, CrossMajorAccessError) as exc:
                _states(context).pop(token, None)
                return await query.answer(str(exc), show_alert=True)
            if not student:
                _states(context).pop(token, None)
                return await query.answer("ကျောင်းသားကို ပြန်ရှာမတွေ့ပါ။", show_alert=True)
            all_funds = await list_fund_options(state["major"], "report")
            names = {str(item["fund_id"]).casefold(): str(item["fund_name"]) for item in all_funds}
            fresh_options = build_undo_options(student, names)
            chosen = next((row for row in fresh_options if row["fund_id"].casefold() == selected["fund_id"].casefold()), None)
            if not chosen:
                _states(context).pop(token, None)
                return await query.answer("ဒီ payment ကို ပြန်ရုပ်သိမ်းနိုင်တော့မည်မဟုတ်ပါ။", show_alert=True)
            identity_keys = ("transaction_id", "transaction_fingerprint", "last_date", "last_amount")
            if any(chosen.get(key) != selected.get(key) for key in identity_keys):
                await query.answer("Payment မှတ်တမ်း ပြောင်းလဲသွားပါတယ်။ /undo ကို ပြန်စပါ။", show_alert=True)
                _states(context).pop(token, None)
                return await query.edit_message_text("⚠️ အတည်ပြုထားတဲ့ payment နဲ့ လက်ရှိမှတ်တမ်း မတူတော့ပါ။ ဘေးကင်းရေးအတွက် Undo မလုပ်ထားပါ။")
            # The original undo service performs another database read before its
            # compare-and-set write. Pass the reviewed transaction identity through
            # user_data so it refuses to undo a newer transaction that arrived in
            # the short interval between this preview check and that database read.
            context.user_data["__plumber_undo_confirmation"] = {
                "actor_id": int(query.from_user.id),
                "major": state["major"],
                "roll_query": roll_query,
                "fund_id": str(selected["fund_id"]),
                "transaction_id": selected.get("transaction_id"),
                "transaction_fingerprint": selected.get("transaction_fingerprint"),
                "date": selected.get("last_date"),
                "amount": selected.get("last_amount"),
            }
            await query.answer("Payment ကို ပြန်စစ်ပြီး Undo လုပ်နေပါသည်...")
            _states(context).pop(token, None)
            try:
                return await _invoke_legacy("undo", query, context, [roll_query, str(selected["fund_id"])])
            finally:
                context.user_data.pop("__plumber_undo_confirmation", None)

    return await query.answer("မသိသော finance picker action ဖြစ်ပါတယ်။", show_alert=True)
