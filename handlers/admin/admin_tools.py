# handlers/admin/admin_tools.py

import os
import csv
import logging
import asyncio
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from database.db import get_db
from utils.auth import is_owner
from utils.helpers import schedule_delete

logger = logging.getLogger(__name__)


async def import_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """CSV ဖိုင်မှ ကျောင်းသားစာရင်းကို MongoDB ထဲသို့ သွင်းမည်"""
    if not is_owner(update.effective_user.id):
        return
    if not update.message.reply_to_message or not update.message.reply_to_message.document:
        await update.message.reply_text("⚠️ ကျေးဇူးပြု၍ CSV ဖိုင်ကို Reply ပြန်ပြီး /import ရိုက်ပါ။")
        return

    doc = update.message.reply_to_message.document
    f_path = f"temp_{doc.file_id}.csv"
    f = await context.bot.get_file(doc.file_id)
    await f.download_to_drive(f_path)

    db = get_db()
    if db is None:
        return

    try:
        count = 0
        with open(f_path, 'r', encoding='utf-8') as csvfile:
            reader = csv.reader(csvfile)
            next(reader, None)
            for row in reader:
                if len(row) < 3:
                    continue
                try:
                    uid = int(row[0])
                    uname = row[1] if row[1] else None
                    fname = row[2]
                    target_chat = update.effective_chat.id

                    await db.group_members.update_one(
                        {"chat_id": target_chat, "user_id": uid},
                        {"$set": {"username": uname, "first_name": fname}},
                        upsert=True
                    )
                    count += 1
                except Exception:
                    continue
        await update.message.reply_text(f"✅ Imported {count} members to Database.")
    except Exception as e:
        logger.error(f"Error importing members: {e}")
    finally:
        if os.path.exists(f_path):
            os.remove(f_path)


async def mention_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Group ထဲရှိ လူအကုန်လုံးကို ၅ ယောက်တစ်တွဲခွဲပြီး Tag တွဲခေါ်မည်"""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    is_authorized = is_owner(user_id)
    if not is_authorized:
        try:
            admins = await context.bot.get_chat_administrators(chat_id)
            admin_ids = [admin.user.id for admin in admins]
            if user_id in admin_ids:
                is_authorized = True
        except Exception as e:
            logger.error(f"Error getting admins: {e}")

    if not is_authorized:
        return

    try:
        await update.message.delete()
    except Exception:
        pass

    db = get_db()
    if db is None:
        return

    members = await db.group_members.find({"chat_id": chat_id}).to_list(length=None)
    if not members:
        msg = await context.bot.send_message(chat_id, "⚠️ Database တွင် Member စာရင်း မရှိသေးပါ။")
        schedule_delete(context, chat_id, msg.message_id, 10)
        return

    delay = 300
    custom_text = update.message.text.replace('/mention', '').strip()
    reason = f"\n📣 <b>{custom_text}</b>" if custom_text else ""

    batch_size = 5
    for i in range(0, len(members), batch_size):
        batch_members = members[i:i+batch_size]

        txt = "<b>🔔 SYSTEM ALERT</b>\n"
        for m in batch_members:
            txt += f'<a href="tg://user?id={m["user_id"]}">👤 {m.get("first_name", "Member")[:5]}</a> '

        txt += f"{reason}\n<pre>Deleting in {delay//60} mins</pre>"

        try:
            sent = await context.bot.send_message(chat_id, txt, parse_mode=ParseMode.HTML)
            schedule_delete(context, chat_id, sent.message_id, delay)
            await asyncio.sleep(2)
        except Exception as e:
            logger.error(f"Mention Error: {e}")


async def get_custom_emoji(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Premium Emoji ID ယူရန် (Owner Only)"""
    if not update.effective_user or not is_owner(update.effective_user.id):
        return

    if not update.message or not update.message.entities:
        return

    for entity in update.message.entities:
        if entity.type == "custom_emoji":
            await update.message.reply_text(
                f"✅ <b>Custom Emoji ID ဖမ်းမိပါပြီ:</b>\n<code>{entity.custom_emoji_id}</code>",
                parse_mode=ParseMode.HTML
            )