# handlers/core/routing.py

import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from database.db import get_db
from utils.constants import MAJORS, MAJOR_GROUP_IDS, get_default_majors_for_group
from utils.auth import is_super_admin
from services.major_service import normalize_major

logger = logging.getLogger(__name__)


def _build_group_route_keyboard(gid: int, allowed: list[str], primary_major: str | None) -> InlineKeyboardMarkup:
    kb = []
    normalized_allowed = {normalize_major(value) or str(value).strip().lower() for value in allowed}
    for m in MAJORS:
        canon = normalize_major(m) or m
        status = "✅" if canon in normalized_allowed else "❌"
        primary_badge = " ⭐ (Primary)" if primary_major and canon == primary_major else ""
        kb.append([
            InlineKeyboardButton(
                f"{status} {m.upper()}{primary_badge}",
                callback_data=f"route_tog_{gid}_{m}"
            )
        ])
    kb.append([InlineKeyboardButton("🔙 Back to Groups", callback_data="route_back")])
    return InlineKeyboardMarkup(kb)


def _major_route_group_ids() -> list[int]:
    return list(dict.fromkeys(gid for ids in MAJOR_GROUP_IDS.values() for gid in ids))


async def route_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_super_admin(update.effective_user.id):
        return await update.message.reply_text("⚠️ Super Admin Only")

    kb = []
    msg = await update.message.reply_text("🔄 Data ဆွဲယူနေပါသည်...")

    for gid in _major_route_group_ids():
        try:
            chat = await context.bot.get_chat(gid)
            title = chat.title[:30] + "..." if len(chat.title) > 30 else chat.title
        except Exception:
            title = f"Group ID: {gid}"
        kb.append([InlineKeyboardButton(f"⚙️ {title}", callback_data=f"route_grp_{gid}")])

    kb.append([InlineKeyboardButton("❌ Cancel", callback_data="route_cancel")])
    await msg.edit_text(
        "🎛 <b>MAJOR GROUP ROUTING SYSTEM</b>\n\n"
        "သတ်မှတ်ထားသော Major သီးသန့် GP များ၏ routing ကိုသာ ပြင်ဆင်နိုင်ပါသည်။\n"
        "CLASS_GROUP_ID shared GP များကို ဒီနေရာတွင် မသုံးပါ။",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.HTML
    )


async def route_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    if not is_super_admin(update.effective_user.id):
        return await query.answer("⚠️ Super Admin Only", show_alert=True)
    await query.answer()

    if data == "route_cancel":
        return await query.edit_message_text("✅ Routing setup closed.")

    db = get_db()

    if data.startswith("route_grp_"):
        gid = int(data.split("_")[2])
        doc = await db.group_routes.find_one({"group_id": gid}) if db is not None else None
        configured = get_default_majors_for_group(gid)
        if doc:
            allowed = doc.get("majors", list(configured) if configured else list(MAJORS))
            primary_major = normalize_major(doc.get("primary_major"))
        else:
            allowed = list(configured) if configured else list(MAJORS)
            primary_major = next(iter(configured)) if len(configured) == 1 else None

        try:
            chat = await context.bot.get_chat(gid)
            title = chat.title
        except Exception:
            title = str(gid)

        primary_txt = f"<code>{primary_major.upper()}</code>" if primary_major else "<i>Shared / Multi-Major</i>"
        await query.edit_message_text(
            f"🎛 <b>ROUTING FOR:</b> {title}\n"
            f"🏛 <b>Authoritative Major:</b> {primary_txt}\n\n"
            f"အောက်ပါ Major များကို အဖွင့်/အပိတ် (Toggle) လုပ်နိုင်ပါသည်။\n"
            f"<i>(Major ၁ ခုတည်းသာ ဖွင့်ထားပါက ထို Group သည် ၎င်း Major ၏ Authoritative Group အလိုအလျောက် ဖြစ်သွားပါမည်)</i>",
            reply_markup=_build_group_route_keyboard(gid, allowed, primary_major),
            parse_mode=ParseMode.HTML
        )

    elif data.startswith("route_tog_"):
        parts = data.split("_")
        gid = int(parts[2])
        major = parts[3]

        doc = await db.group_routes.find_one({"group_id": gid}) if db is not None else None
        configured = get_default_majors_for_group(gid)
        default_allowed = list(configured) if configured else list(MAJORS)
        allowed = list(doc.get("majors", default_allowed)) if doc else default_allowed
        allowed = list({normalize_major(value) for value in allowed if normalize_major(value)})

        if major in allowed:
            allowed.remove(major)
        else:
            allowed.append(major)

        # Synchronize primary_major: if exactly 1 major is active, bind it as primary_major
        normalized_allowed = list({normalize_major(m) for m in allowed if normalize_major(m)})
        primary_major = normalized_allowed[0] if len(normalized_allowed) == 1 else None

        if db is not None:
            update_doc = {"$set": {"majors": allowed, "primary_major": primary_major}}
            await db.group_routes.update_one({"group_id": gid}, update_doc, upsert=True)

        await query.edit_message_reply_markup(
            reply_markup=_build_group_route_keyboard(gid, allowed, primary_major)
        )

    elif data == "route_back":
        kb = []
        for gid in _major_route_group_ids():
            try:
                chat = await context.bot.get_chat(gid)
                title = chat.title[:30] + "..." if len(chat.title) > 30 else chat.title
            except Exception:
                title = f"Group ID: {gid}"
            kb.append([InlineKeyboardButton(f"⚙️ {title}", callback_data=f"route_grp_{gid}")])
        kb.append([InlineKeyboardButton("❌ Cancel", callback_data="route_cancel")])
        await query.edit_message_text(
            "🎛 <b>MAJOR GROUP ROUTING SYSTEM</b>\n\n"
            "သတ်မှတ်ထားသော Major သီးသန့် GP များ၏ routing ကိုသာ ပြင်ဆင်နိုင်ပါသည်။\n"
            "CLASS_GROUP_ID shared GP များကို ဒီနေရာတွင် မသုံးပါ။",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.HTML
        )