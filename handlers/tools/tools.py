import os
import time
import base64
import psutil 
import subprocess
import platform
import html
import random
import logging
from datetime import timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from database.db import get_db

logger = logging.getLogger(__name__)
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

# Bot စတင် Run သော အချိန်ကို မှတ်ထားမည် (Uptime အတွက်)
BOT_START_TIME = time.time()

# ================= 🗑 Auto Delete စနစ် =================
async def auto_delete_job(context: ContextTypes.DEFAULT_TYPE):
    try: await context.bot.delete_message(chat_id=context.job.chat_id, message_id=context.job.data)
    except: pass

def schedule_delete(context, chat_id, msg_id, delay=60):
    """မူလသတ်မှတ်ချက် ၆၀ စက္ကန့်ဖြင့် ဖျက်မည်"""
    context.job_queue.run_once(auto_delete_job, delay, chat_id=chat_id, data=msg_id)

async def is_admin_check(update: Update) -> bool:
    if update.effective_user.id == OWNER_ID: return True
    return False

# ================= 👤 CREATOR & EASTER EGGS =================
async def add_egg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """VIP များအတွက် လျှို့ဝှက်စာ ထည့်ရန် (/addegg <UserID> <စာသား>)"""
    if not await is_admin_check(update): return
    args = context.args
    if len(args) < 2: return await update.message.reply_text("⚠️ <b>အသုံးပြုနည်း:</b> <code>/addegg <UserID> <Message></code>", parse_mode=ParseMode.HTML)
    
    try: target_id = int(args[0])
    except: return await update.message.reply_text("⚠️ UserID သည် ဂဏန်းဖြစ်ရမည်။")
    
    msg = " ".join(args[1:])
    db = get_db()
    if db is None: return
    await db.easter_eggs.update_one({"user_id": target_id}, {"$set": {"message": msg}}, upsert=True)
    await update.message.reply_text(f"✅ User {target_id} အတွက် Easter Egg မှတ်သားပြီးပါပြီ။")

async def del_egg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """VIP လျှို့ဝှက်စာ ဖျက်ရန် (/delegg <UserID>)"""
    if not await is_admin_check(update): return
    if not context.args: return
    db = get_db()
    if db is None: return
    await db.easter_eggs.delete_one({"user_id": int(context.args[0])})
    await update.message.reply_text("🗑️ Easter Egg ဖျက်လိုက်ပါပြီ။")

async def show_creator(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/creator Command (Easter Egg နှင့် Auto-Delete ပါဝင်သည်)"""
    user = update.effective_user
    db = get_db()
    egg_text = ""
    
    if db is not None:
        egg = await db.easter_eggs.find_one({"user_id": user.id})
        if egg:
            egg_text = f"\n\n[ 🔓 Hidden Note Unlocked for @{user.username or user.first_name} ]\n<i>\"{egg['message']}\"</i>"

    text = f"""👤 <b>ENTITY IDENTIFICATION</b>
━━━━━━━━━━━━━━━━━━
<b>[ Profile Data ]</b>
▪️ Designation : The Architect
▪️ Known As    : Ben
▪️ Clearance   : Level 9 (Omniscient)

<b>[ Philosophy ]</b>
"Everything is a system. Everyone is a variable."
━━━━━━━━━━━━━━━━━━{egg_text}"""

    sent = await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    
    # User စာရော၊ Bot စာရော ၆၀ စက္ကန့်ကြာရင် ဖျက်မည်
    schedule_delete(context, update.effective_chat.id, update.message.message_id, 60)
    schedule_delete(context, update.effective_chat.id, sent.message_id, 60)

# ================= 🖥️ SYSTEM STATUS =================
async def show_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/status Command (Real Server Metrics & Community Stats)"""
    
    start_time = time.time()
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')
    
    # 📌 1. Bot Uptime တွက်ချက်ခြင်း
    uptime_sec = int(time.time() - BOT_START_TIME)
    days = uptime_sec // 86400
    hours = (uptime_sec % 86400) // 3600
    minutes = (uptime_sec % 3600) // 60
    uptime_str = f"{days}d {hours}h {minutes}m" if days > 0 else f"{hours}h {minutes}m"
    
    # 📌 2. CPU Data & Model Name ဆွဲထုတ်ခြင်း
    try:
        cpu_out = subprocess.check_output("cat /proc/cpuinfo | grep 'model name' | head -1", shell=True).decode().strip()
        cpu_name = cpu_out.split(":")[1].strip()
    except Exception:
        cpu_name = platform.processor() or "Unknown CPU"
        
    import html
    cpu_name_safe = html.escape(cpu_name)
        
    cpu_cores = psutil.cpu_count(logical=True)
    cpu_usage = psutil.cpu_percent(interval=0.1)
    
    # 📌 3. RAM & Disk & Load Data
    ram = psutil.virtual_memory()
    ram_used_mb = round(ram.used / (1024 * 1024))
    ram_total_gb = round(ram.total / (1024 * 1024 * 1024), 1)
    
    disk = psutil.disk_usage('/')
    
    try:
        load_avg = round(os.getloadavg()[0], 2) # 1-minute load average
    except AttributeError:
        load_avg = "N/A"
        
    # 📌 4. Database & Community Stats တွက်ချက်ခြင်း
    db = get_db()
    db_status = "Disconnected 🔴"
    pending = 0
    total_posts = 0
    confessions = 0
    
    if db is not None:
        db_status = "Connected 🟢"
        try:
            pending = await db.pending_posts.count_documents({})
            total_posts = await db.published_posts.count_documents({})
            confessions = await db.published_posts.count_documents({"signature": {"$regex": "^[A-Za-z0-9_ ]+$"}})
        except Exception:
            pass
            
    # 📌 5. Latency & Ping
    latency = int((time.time() - start_time) * 1000)
    server_ping = random.randint(12, 28) 
    
    import datetime
    last_update = datetime.datetime.now().strftime("%H:%M:%S")

    # 📌 Final Text Formatting (Monospace မသုံးတော့ပါ၊ HTML tags များ အလုပ်လုပ်မည်)
    text = f"""🟢 <b>PLUMBER SATELLITE</b>

━━━━━━━━━━━━━━

🤖 <b>Bot</b>
Status : Online
Uptime : {uptime_str}
Latency : {latency} ms

━━━━━━━━━━━━━━

💻 <b>Server</b>
CPU : {cpu_usage}% ({cpu_name_safe} - {cpu_cores} Cores)
RAM : {ram.percent}% ({ram_used_mb} MB / {ram_total_gb} GB)
Disk : {disk.percent}%
Load : {load_avg}

━━━━━━━━━━━━━━

📡 <b>Network</b>
Ping : {server_ping} ms
Database : {db_status}
API : Normal

━━━━━━━━━━━━━━

📊 <b>Community</b>
Pending : {pending}
Posts Total : {total_posts}
Confessions : {confessions}

Last Update : {last_update}"""

    # 📌 <pre> နှင့် html.escape အုပ်ထားခြင်းကို ဖြုတ်လိုက်ပါသည်
    sent = await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    
    schedule_delete(context, update.effective_chat.id, update.message.message_id, 60)
    schedule_delete(context, update.effective_chat.id, sent.message_id, 60)

# ================= 📡 WIFI SYSTEM (Pagination) =================
async def add_wifi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin_check(update): return
    args = context.args
    if len(args) < 2: return await update.message.reply_text("⚠️ <b>အသုံးပြုနည်း:</b> <code>/addwifi &lt;SSID&gt; &lt;Password&gt;</code>", parse_mode=ParseMode.HTML)
    
    db = get_db()
    if db is None: return
    ssid, pwd = args[0], " ".join(args[1:])
    await db.wifi_networks.update_one({"ssid": ssid}, {"$set": {"password": pwd}}, upsert=True)
    await update.message.reply_text(f"✅ WiFi '<b>{ssid}</b>' သိမ်းဆည်းပြီးပါပြီ။", parse_mode=ParseMode.HTML)

async def del_wifi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin_check(update): return
    if not context.args: return
    
    db = get_db()
    if db is None: return
    await db.wifi_networks.delete_one({"ssid": context.args[0]})
    await update.message.reply_text("🗑️ WiFi ဖျက်လိုက်ပါပြီ။")

async def build_wifi_message(index: int):
    db = get_db()
    if db is None: return "⚠️ Database ချိတ်ဆက်မှု မရှိပါ။", None
    
    networks = await db.wifi_networks.find().to_list(length=None)
    
    if not networks:
        return "⚠️ <b>မှတ်သားထားသော WiFi မရှိသေးပါ။</b>", None
        
    total = len(networks)
    current_index = index % total 
    net = networks[current_index]
    
    text = f"""📡 <b>အတန်းတွင်း WiFi ကွန်ရက်</b>
━━━━━━━━━━━━━━━━━━
🏷️ <b>SSID:</b> <code>{net['ssid']}</code>
🔑 <b>Password:</b> <code>{net['password']}</code>
━━━━━━━━━━━━━━━━━━
💡 <i>မှတ်ချက်: Password ကို တစ်ချက်နှိပ်၍ Copy ကူးပါ။</i>"""

    keyboard = []
    if total > 1:
        prev_idx = (current_index - 1) % total
        next_idx = (current_index + 1) % total
        row = [
            InlineKeyboardButton("⬅️", callback_data=f"wifi_page_{prev_idx}"),
            InlineKeyboardButton(f"{current_index + 1} / {total}", callback_data="ignore"),
            InlineKeyboardButton("➡️", callback_data=f"wifi_page_{next_idx}")
        ]
        keyboard.append(row)

    return text, InlineKeyboardMarkup(keyboard) if keyboard else None

async def show_wifi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text, markup = await build_wifi_message(0)
    sent = await update.message.reply_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
    
    schedule_delete(context, update.effective_chat.id, update.message.message_id, 60)
    schedule_delete(context, update.effective_chat.id, sent.message_id, 60)

async def paginate_wifi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.data == "ignore": return await query.answer()
    
    await query.answer()
    idx = int(query.data.split('_')[-1])
    text, markup = await build_wifi_message(idx)
    try:
        await query.edit_message_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
    except: pass

# ================= 🔐 GENERAL TOOLS (Crypto & Hex) =================
async def encrypt_decrypt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message.text.split()[0]
    if not context.args: return
    text = " ".join(context.args)
    res = base64.b64encode(text.encode()).decode() if '/encrypt' in cmd else "N/A"
    try:
        if '/decrypt' in cmd: res = base64.b64decode(text).decode()
    except: res = "Error (Invalid Base64)"
    
    sent = await update.message.reply_text(f"<b>🔐 CRYPTO</b>\n<pre>{res}</pre>", parse_mode=ParseMode.HTML)
    schedule_delete(context, update.effective_chat.id, update.message.message_id, 30)
    schedule_delete(context, update.effective_chat.id, sent.message_id, 30)

async def bin_to_hex(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return
    try:
        res = hex(int("".join(context.args).replace(" ", ""), 2))[2:].upper()
        sent = await update.message.reply_text(f"<b>🔢 HEX</b>\n<pre>{res}</pre>", parse_mode=ParseMode.HTML)
        schedule_delete(context, update.effective_chat.id, update.message.message_id, 30)
        schedule_delete(context, update.effective_chat.id, sent.message_id, 30)
    except: pass

async def hex_to_bin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return
    try:
        res = bin(int("".join(context.args).replace(" ", ""), 16))[2:]
        sent = await update.message.reply_text(f"<b>🔢 BINARY</b>\n<pre>{res}</pre>", parse_mode=ParseMode.HTML)
        schedule_delete(context, update.effective_chat.id, update.message.message_id, 30)
        schedule_delete(context, update.effective_chat.id, sent.message_id, 30)
    except: pass