import os
import logging
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from database.db import get_db

logger = logging.getLogger(__name__)
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
FOOTER = "\n\n<pre>System by Ben | Omni-Net 🟢</pre>"

# ================= 🗑 Auto Delete စနစ် =================
async def auto_delete_job(context: ContextTypes.DEFAULT_TYPE):
    try: await context.bot.delete_message(chat_id=context.job.chat_id, message_id=context.job.data)
    except: pass

def schedule_delete(context, chat_id, msg_id, delay):
    context.job_queue.run_once(auto_delete_job, delay, chat_id=chat_id, data=msg_id)

# ================= 🛡️ Admin စစ်ဆေးခြင်း =================
async def is_admin_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id
    if user_id == OWNER_ID: return True
    if update.effective_chat.type in ['group', 'supergroup']:
        try:
            admins = await context.bot.get_chat_administrators(update.effective_chat.id)
            admin_ids = [admin.user.id for admin in admins]
            if user_id in admin_ids: return True
        except: pass
    return False

# ================= ⚙️ Custom Commands ထည့်ရန်/ဖျက်ရန် =================
async def add_custom_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/addcmd1, /addcmd2, /addcmd3 ဖြင့် Bot ကို စာသင်ပေးရန်"""
    msg = update.message
    try: await msg.delete()
    except: pass
    
    if not await is_admin_check(update, context): return
    if len(context.args) < 2: 
        warn = await context.bot.send_message(update.effective_chat.id, "⚠️ အသုံးပြုနည်း: <code>/addcmd1 &lt;trigger&gt; &lt;စာသား&gt;</code>", parse_mode=ParseMode.HTML)
        schedule_delete(context, update.effective_chat.id, warn.message_id, 5)
        return

    cmd_text = msg.text.split()[0].lower()
    style = 1 if '1' in cmd_text else (2 if '2' in cmd_text else 3)
    trigger = context.args[0].lower().replace('/', '')
    response = " ".join(context.args[1:])
    
    file_id = None
    # Style 3 ဖြစ်ပါက ပုံကို Reply ပြန်ထားခြင်း ရှိမရှိ စစ်ဆေးမည်
    if style == 3:
        if msg.reply_to_message and msg.reply_to_message.photo:
            file_id = msg.reply_to_message.photo[-1].file_id
        else:
            warn = await context.bot.send_message(update.effective_chat.id, "⚠️ Style 3 အတွက် ပုံကို Reply ပြန်ပြီး Command ရိုက်ပါ။")
            schedule_delete(context, update.effective_chat.id, warn.message_id, 5)
            return

    db = get_db()
    if db is None: return

    # MongoDB တွင် သိမ်းဆည်းမည်
    await db.custom_cmds.update_one(
        {"trigger": trigger},
        {"$set": {"response": response, "style": style, "file_id": file_id}},
        upsert=True
    )
    
    success = await context.bot.send_message(update.effective_chat.id, f"<pre>💾 Saved /{trigger}</pre>", parse_mode=ParseMode.HTML)
    schedule_delete(context, update.effective_chat.id, success.message_id, 5)

async def del_custom_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/delcmd ဖြင့် ပြန်ဖျက်ရန်"""
    msg = update.message
    try: await msg.delete()
    except: pass
    
    if not await is_admin_check(update, context): return
    if not context.args: return
    
    trigger = context.args[0].lower().replace('/', '')
    db = get_db()
    if db is None: return
    
    await db.custom_cmds.delete_one({"trigger": trigger})
    success = await context.bot.send_message(update.effective_chat.id, f"<pre>🗑️ Deleted /{trigger}</pre>", parse_mode=ParseMode.HTML)
    schedule_delete(context, update.effective_chat.id, success.message_id, 5)

# ================= 🚀 User မှ Command ခေါ်သုံးသည့်အခါ =================
async def handle_custom_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User က / ဖြင့်စသော စကားလုံးများရိုက်တိုင်း Database တွင် လိုက်ရှာပေးမည့်စနစ်"""
    if not update.message or not update.message.text: return
    text = update.message.text
    if not text.startswith('/'): return
    
    trigger = text.split()[0][1:].lower()
    db = get_db()
    if db is None: return
    
    # Database ထဲတွင် အဆိုပါ Command ရှိမရှိ ရှာမည်
    res = await db.custom_cmds.find_one({"trigger": trigger})
    if res:
        sent = None
        if res['style'] == 1:
            sent = await update.message.reply_text(f"<b>🟢 SYSTEM RESPONSE</b>\n<pre>{res['response']}</pre>{FOOTER}", parse_mode=ParseMode.HTML)
        elif res['style'] == 2:
            sent = await update.message.reply_text(res['response'])
        elif res['style'] == 3:
            try: 
                sent = await context.bot.send_photo(update.effective_chat.id, photo=res['file_id'], caption=res['response'])
            except: pass
            
        # User ရိုက်သောစာ နှင့် Bot ပြန်သောစာ နှစ်ခုလုံးကို ၃၀ စက္ကန့်အကြာတွင် ဖျက်မည်
        if sent:
            schedule_delete(context, update.effective_chat.id, update.message.message_id, 30)
            schedule_delete(context, update.effective_chat.id, sent.message_id, 30)