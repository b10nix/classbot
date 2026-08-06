import os
import csv
import logging
import asyncio
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from database.db import get_db
from utils.auth import is_owner

logger = logging.getLogger(__name__)
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

async def auto_delete_job(context: ContextTypes.DEFAULT_TYPE):
    try: await context.bot.delete_message(chat_id=context.job.chat_id, message_id=context.job.data)
    except: pass

def schedule_delete(context, chat_id, msg_id, delay):
    context.job_queue.run_once(auto_delete_job, delay, chat_id=chat_id, data=msg_id)

async def import_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """CSV ဖိုင်မှ ကျောင်းသားစာရင်းကို MongoDB ထဲသို့ သွင်းမည်"""
    if update.effective_user.id != OWNER_ID: return
    if not update.message.reply_to_message or not update.message.reply_to_message.document: 
        await update.message.reply_text("⚠️ ကျေးဇူးပြု၍ CSV ဖိုင်ကို Reply ပြန်ပြီး /import ရိုက်ပါ။")
        return
    
    doc = update.message.reply_to_message.document
    f_path = f"temp_{doc.file_id}.csv"
    f = await context.bot.get_file(doc.file_id)
    await f.download_to_drive(f_path)
    
    db = get_db()
    if db is None: return

    try:
        count = 0
        with open(f_path, 'r', encoding='utf-8') as csvfile:
            reader = csv.reader(csvfile)
            next(reader, None) 
            for row in reader:
                if len(row) < 3: continue
                try:
                    uid = int(row[0])
                    uname = row[1] if row[1] else None
                    fname = row[2]
                    target_chat = update.effective_chat.id
                    
                    # MongoDB သို့ Group Members များ သိမ်းဆည်းခြင်း
                    await db.group_members.update_one(
                        {"chat_id": target_chat, "user_id": uid},
                        {"$set": {"username": uname, "first_name": fname}},
                        upsert=True
                    )
                    count += 1
                except: continue
        await update.message.reply_text(f"✅ Imported {count} members to Database.")
    except Exception as e: 
        logger.error(e)
    finally:
        if os.path.exists(f_path): os.remove(f_path)

async def mention_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Group ထဲရှိ လူအကုန်လုံးကို ၅ ယောက်တစ်ခွဲပြီး Tag တွဲခေါ်မည်"""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    # 1. Group Admin ဟုတ်/မဟုတ် စစ်ဆေးခြင်း
    is_authorized = False
    if user_id == OWNER_ID:
        is_authorized = True
    else:
        try:
            admins = await context.bot.get_chat_administrators(chat_id)
            admin_ids = [admin.user.id for admin in admins]
            if user_id in admin_ids:
                is_authorized = True
        except Exception as e:
            logger.error(f"Error getting admins: {e}")

    if not is_authorized: return

    try: await update.message.delete() 
    except: pass
    
    db = get_db()
    if db is None: return
    
    cursor = db.group_members.find({"chat_id": chat_id})
    members = await cursor.to_list(length=None)
    
    if not members: 
        msg = await context.bot.send_message(chat_id, "⚠️ Database တွင် Member စာရင်း မရှိသေးပါ။")
        schedule_delete(context, chat_id, msg.message_id, 10)
        return
    
    # 3. ၅ ယောက်တစ်တွဲခွဲ၍ စာပို့ခြင်း (Notification သေချာ မြည်စေရန်)
    delay = 300 # ၃၀၀ စက္ကန့် (၅ မိနစ်)
    
    # Message ထဲတွင် ထည့်လိုသော အကြောင်းအရာရှိပါက
    custom_text = update.message.text.replace('/mention', '').strip()
    reason = f"\n📣 <b>{custom_text}</b>" if custom_text else ""
    
    # လူ (၅) ယောက်စီ ခွဲထုတ်ခြင်း (Batching)
    batch_size = 5
    for i in range(0, len(members), batch_size):
        batch_members = members[i:i+batch_size]
        
        txt = "<b>🔔 SYSTEM ALERT</b>\n"
        for m in batch_members:
            # မြင်သာစေရန် နာမည်အတို သို့မဟုတ် သင်္ကေတ သုံးနိုင်သည်
            txt += f'<a href="tg://user?id={m["user_id"]}">👤 {m.get("first_name", "Member")[:5]}</a> '
        
        txt += f"{reason}\n<pre>Deleting in {delay//60} mins</pre>"
        
        try:
            sent = await context.bot.send_message(chat_id, txt, parse_mode=ParseMode.HTML)
            schedule_delete(context, chat_id, sent.message_id, delay)
            # FloodWait (Ban ခံရခြင်း) မှ ကာကွယ်ရန် စာတစ်စောင်နှင့် တစ်စောင်ကြား ၂ စက္ကန့် နားမည်
            await asyncio.sleep(2) 
        except Exception as e:
            logger.error(f"Mention Error: {e}")

# ================= 🌟 Premium Emoji ID ယူရန် (Admin Only) =================
async def get_custom_emoji(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ❌ အဟောင်း: if not await is_owner(update):
    # ✅ အသစ်: await ကို ဖြုတ်လိုက်ပါ (အောက်ပါအတိုင်း ပြင်ပါ)
    if not is_owner(update): 
        return
        
    if not update.message or not update.message.entities:
        return

    for entity in update.message.entities:
        if entity.type == "custom_emoji":
            await update.message.reply_text(
                f"✅ <b>Custom Emoji ID ဖမ်းမိပါပြီ:</b>\n<code>{entity.custom_emoji_id}</code>",
                parse_mode=ParseMode.HTML
            )