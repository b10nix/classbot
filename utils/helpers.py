# utils/helpers.py

import html
import random
import logging
import re
from datetime import datetime, timezone, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode

from database.db import get_db
from utils.constants import CLASS_GROUP_IDS, VIP_USERS, RANDOM_EMOJIS
from utils.auth import is_super_admin

logger = logging.getLogger(__name__)
GROUP_NAME_CACHE = {}


# ================= 🛡️ CANCEL CONVERSATION =================
async def cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text("<pre>🚫 Canceled.</pre>", parse_mode=ParseMode.HTML)
        elif update.message:
            await update.message.reply_text("<pre>🚫 Canceled.</pre>", parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Error in cancel_conv: {e}")
    return ConversationHandler.END


# ================= 🗑️ CENTRALIZED AUTO DELETE HELPERS =================
async def auto_delete_job(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    msg_ids = data if isinstance(data, (list, tuple)) else [data]
    for m_id in msg_ids:
        try:
            await context.bot.delete_message(chat_id=context.job.chat_id, message_id=m_id)
        except Exception:
            pass

def schedule_delete(context: ContextTypes.DEFAULT_TYPE, chat_id: int, msg_ids: int | list[int], delay: int = 300):
    """Universal message deletion scheduler supporting single ID or list of IDs."""
    if context.job_queue:
        context.job_queue.run_once(auto_delete_job, delay, chat_id=chat_id, data=msg_ids)


# ================= 🕵️‍♂️ CENTRALIZED EC RESOLVER =================
async def resolve_ec(identifier: str | int) -> tuple[int | None, str]:
    """Resolve an EC from ec_roles, a numeric Telegram ID, or legacy EC records."""
    raw_str = str(identifier).strip()
    clean_key = raw_str.lower().lstrip("@")
    db = get_db()

    if db is not None:
        if clean_key.isdigit():
            role = await db.ec_roles.find_one({"user_id": int(clean_key), "active": {"$ne": False}})
        else:
            role = await db.ec_roles.find_one({
                "nickname": {"$regex": f"^{re.escape(clean_key)}$", "$options": "i"},
                "active": {"$ne": False},
            })
        if role and role.get("user_id") is not None:
            return int(role["user_id"]), str(role.get("nickname") or raw_str)

    # Numeric identifiers remain accepted by commands that explicitly ask for an ID.
    if clean_key.isdigit():
        return int(clean_key), raw_str

    # Compatibility lookup in existing finance/student records. No hard-coded EC map.
    if db is not None:
        regex_q = {"$regex": f"^{re.escape(clean_key)}$", "$options": "i"}
        target_ec = await db.approvers.find_one({"$or": [{"username": regex_q}, {"name": regex_q}]})
        if target_ec:
            uid = target_ec.get("user_id")
            if uid is not None:
                return int(uid), target_ec.get("name") or clean_key

        ledger_ec = await db.ec_ledgers.find_one({"username": regex_q})
        if ledger_ec:
            uid = ledger_ec.get("ec_id") or ledger_ec.get("user_id")
            if uid:
                return int(uid), ledger_ec.get("username", clean_key)

        student_ec = await db.student_info.find_one({"$or": [{"tg_username": regex_q}, {"name": regex_q}]})
        if student_ec:
            uid = student_ec.get("tg_id") or student_ec.get("user_id")
            if uid:
                return int(uid), student_ec.get("name") or clean_key

    return None, raw_str


# ================= 👤 USER INFO HELPERS =================
def get_safe_user_link(user_entity) -> str:
    if not user_entity:
        return "Unknown User"

    user_id = None
    first_name = "User Profile"
    username = None

    if hasattr(user_entity, "id"):
        user_id = user_entity.id
        first_name = user_entity.first_name or f"User {user_id}"
        username = user_entity.username
    elif isinstance(user_entity, dict):
        user_id = (
            user_entity.get("sender_id")
            or user_entity.get("tg_id")
            or user_entity.get("user_id")
        )
        first_name = (
            user_entity.get("sender_first_name")
            or user_entity.get("first_name")
            or user_entity.get("name")
            or f"User {user_id or ''}"
        )
        username = (
            user_entity.get("sender_username")
            or user_entity.get("tg_username")
            or user_entity.get("username")
        )

    safe_first_name = html.escape(str(first_name))

    if username and str(username) != "None":
        clean_username = str(username).lstrip("@").strip()
        if clean_username and clean_username.lower() != "unknown":
            safe_username = html.escape(clean_username)
            return f'<a href="https://t.me/{safe_username}">{safe_first_name}</a> (@{safe_username})'

    if user_id is not None:
        return f'<a href="tg://user?id={user_id}">{safe_first_name}</a> (No Username)'

    return safe_first_name


async def sync_user_username(user_entity) -> bool:
    """Refresh a user's last-observed Telegram username using their stable Telegram ID.

    This updates student lookup records and any pending post/confession snapshots so
    moderation logs don't keep showing an old username after the user next interacts
    with the bot. A missing Telegram username is stored as None when it differs from
    the last known value.
    """
    if user_entity is None:
        return False

    user_id = getattr(user_entity, "id", None)
    if user_id is None:
        return False

    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        return False

    # Telegram User.username is either a current username without '@' or None.
    username = getattr(user_entity, "username", None)
    timestamp = datetime.now(timezone.utc)
    db = get_db()
    if db is None:
        return False

    try:
        await db.student_info.update_many(
            {
                "$or": [{"tg_id": user_id}, {"user_id": user_id}],
                "tg_username": {"$ne": username},
            },
            {"$set": {"tg_username": username, "username_updated_at": timestamp}},
        )
    except Exception as exc:
        # Keep this global hook non-blocking for unrelated bot features.
        logger.warning("Could not sync username for Telegram user %s in student_info: %s", user_id, exc)

    try:
        await db.pending_posts.update_many(
            {
                "sender_id": user_id,
                "sender_username": {"$ne": username},
            },
            {"$set": {"sender_username": username, "sender_username_updated_at": timestamp}},
        )
    except Exception as exc:
        logger.warning("Could not sync username for Telegram user %s in pending_posts: %s", user_id, exc)

    return True


async def sync_user_username_on_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global Update handler: refresh the username whenever a user interacts."""
    user = getattr(update, "effective_user", None)
    if user is not None:
        await sync_user_username(user)


# ================== 👤 USER IDENTITY HELPERS =================
async def get_user_identity(user_id: int) -> str:
    db = get_db()
    if db is None:
        return "👤"

    user_data = await db.user_settings.find_one({"user_id": user_id})
    if user_data:
        if "nickname" in user_data and user_data["nickname"]:
            return html.escape(user_data["nickname"])
        if "emoji" in user_data and user_data["emoji"]:
            return user_data["emoji"]

    new_emoji = random.choice(RANDOM_EMOJIS)
    await db.user_settings.update_one({"user_id": user_id}, {"$set": {"emoji": new_emoji}}, upsert=True)
    return new_emoji


# ================= 🕒 TIME HELPERS =================
def get_mm_time() -> str:
    mm_tz = timezone(timedelta(hours=6, minutes=30))
    return datetime.now(mm_tz).strftime("%Y-%m-%d %I:%M:%S %p")


# ================= 🎯 MULTI-GROUP HELPERS (SECURITY CORE) =================
async def get_allowed_groups(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> list[int]:
    if user_id in VIP_USERS or is_super_admin(user_id):
        return CLASS_GROUP_IDS

    allowed = []
    for gid in CLASS_GROUP_IDS:
        try:
            member = await context.bot.get_chat_member(chat_id=gid, user_id=user_id)
            if member.status in ['member', 'administrator', 'creator', 'restricted']:
                allowed.append(gid)
        except Exception:
            pass
    return allowed


async def build_target_keyboard(user_id: int, context: ContextTypes.DEFAULT_TYPE, prefix_send: str, prefix_cancel: str):
    allowed_groups = await get_allowed_groups(user_id, context)
    keyboard = []
    for gid in allowed_groups:
        try:
            chat = await context.bot.get_chat(gid)
            title = chat.title[:25] + "..." if len(chat.title) > 25 else chat.title
        except Exception:
            title = f"Group ID: {gid}"
        keyboard.append([InlineKeyboardButton(f"📤 SEND TO: {title}", callback_data=f"{prefix_send}_{gid}")])

    if len(allowed_groups) > 1:
        keyboard.append([InlineKeyboardButton("🌍 SEND TO: ALL ALLOWED GROUPS", callback_data=f"{prefix_send}_all")])

    keyboard.append([InlineKeyboardButton("❌ ABORT", callback_data=prefix_cancel)])
    return InlineKeyboardMarkup(keyboard)


# ================= GROUP NAME HELPERS =================
async def get_group_name(bot, group_id: int) -> str:
    if group_id in GROUP_NAME_CACHE:
        return GROUP_NAME_CACHE[group_id]

    try:
        chat = await bot.get_chat(group_id)
        group_name = chat.title or str(group_id)
        GROUP_NAME_CACHE[group_id] = group_name
        return group_name
    except Exception:
        return str(group_id)