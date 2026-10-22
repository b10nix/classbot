# handlers/core/custom_cmds.py

import os
import logging
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from database.db import get_db
from utils.auth import is_super_admin
from utils.helpers import schedule_delete

logger = logging.getLogger(__name__)
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
FOOTER = "\n\n<pre>System by Ben | Omni-Net 🟢</pre>"


async def is_admin_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id
    if is_super_admin(user_id):
        return True
    if update.effective_chat.type in ['group', 'supergroup']:
        try:
            admins = await context.bot.get_chat_administrators(update.effective_chat.id)
            admin_ids = [admin.user.id for admin in admins]
            if user_id in admin_ids:
                return True
        except Exception:
            pass
    return False


async def add_custom_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/addcmd1, /addcmd2, /addcmd3 ဖြင့် Bot ကို စာသင်ပေးရန်"""
    msg = update.message
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    try:
        await msg.delete()
    except Exception:
        pass

    if not await is_admin_check(update, context):
        return
    if len(context.args) < 2:
        warn = await context.bot.send_message(
            chat_id,
            "⚠️ အသုံးပြုနည်း: <code>/addcmd1 &lt;trigger&gt; &lt;စာသား&gt;</code>",
            parse_mode=ParseMode.HTML
        )
        schedule_delete(context, chat_id, warn.message_id, 5)
        return

    cmd_text = msg.text.split()[0].lower()
    style = 1 if '1' in cmd_text else (2 if '2' in cmd_text else 3)
    trigger = context.args[0].lower().replace('/', '')
    response = " ".join(context.args[1:])

    file_id = None
    if style == 3:
        if msg.reply_to_message and msg.reply_to_message.photo:
            file_id = msg.reply_to_message.photo[-1].file_id
        else:
            warn = await context.bot.send_message(chat_id, "⚠️ Style 3 အတွက် ပုံကို Reply ပြန်ပြီး Command ရိုက်ပါ။")
            schedule_delete(context, chat_id, warn.message_id, 5)
            return

    db = get_db()
    if db is None:
        return

    # Super Admins in private chat create global triggers (scope_chat_id=0);
    # Group Admins create triggers scoped to their own group chat_id.
    scope_chat_id = 0 if (update.effective_chat.type == "private" and is_super_admin(user_id)) else chat_id

    await db.custom_cmds.update_one(
        {"trigger": trigger, "chat_id": scope_chat_id},
        {"$set": {
            "trigger": trigger,
            "chat_id": scope_chat_id,
            "response": response,
            "style": style,
            "file_id": file_id
        }},
        upsert=True
    )

    success = await context.bot.send_message(chat_id, f"<pre>💾 Saved /{trigger}</pre>", parse_mode=ParseMode.HTML)
    schedule_delete(context, chat_id, success.message_id, 5)


async def del_custom_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/delcmd ဖြင့် ပြန်ဖျက်ရန်"""
    msg = update.message
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    try:
        await msg.delete()
    except Exception:
        pass

    if not await is_admin_check(update, context):
        return
    if not context.args:
        return

    trigger = context.args[0].lower().replace('/', '')
    db = get_db()
    if db is None:
        return

    if is_super_admin(user_id) and update.effective_chat.type == "private":
        await db.custom_cmds.delete_many({"trigger": trigger})
    else:
        await db.custom_cmds.delete_one({"trigger": trigger, "chat_id": chat_id})

    success = await context.bot.send_message(chat_id, f"<pre>🗑️ Deleted /{trigger}</pre>", parse_mode=ParseMode.HTML)
    schedule_delete(context, chat_id, success.message_id, 5)


async def handle_custom_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User က / ဖြင့်စသော စကားလုံးများရိုက်တိုင်း Database တွင် လိုက်ရှာပေးမည့်စနစ်"""
    if not update.message or not update.message.text:
        return
    text = update.message.text
    if not text.startswith('/'):
        return

    trigger = text.split()[0][1:].split('@')[0].lower()
    chat_id = update.effective_chat.id
    db = get_db()
    if db is None:
        return

    # 1. Check group-specific trigger first, then fallback to global/legacy trigger
    res = await db.custom_cmds.find_one({"trigger": trigger, "chat_id": chat_id})
    if not res:
        res = await db.custom_cmds.find_one({
            "trigger": trigger,
            "$or": [{"chat_id": 0}, {"chat_id": {"$exists": False}}]
        })

    if res:
        sent = None
        if res['style'] == 1:
            sent = await update.message.reply_text(
                f"<b>🟢 SYSTEM RESPONSE</b>\n<pre>{res['response']}</pre>{FOOTER}",
                parse_mode=ParseMode.HTML
            )
        elif res['style'] == 2:
            sent = await update.message.reply_text(res['response'])
        elif res['style'] == 3:
            try:
                sent = await context.bot.send_photo(chat_id, photo=res['file_id'], caption=res['response'])
            except Exception:
                pass

        if sent:
            schedule_delete(context, chat_id, [update.message.message_id, sent.message_id], 30)