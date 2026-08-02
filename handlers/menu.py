import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

# 🛡️ Centralized Auth ကို ခေါ်ယူအသုံးပြုခြင်း
from utils.auth import is_super_admin

logger = logging.getLogger(__name__)

# ================= 🗑 Auto Delete စနစ် =================
async def auto_delete_job(context: ContextTypes.DEFAULT_TYPE):
    try: await context.bot.delete_message(chat_id=context.job.chat_id, message_id=context.job.data)
    except: pass

def schedule_delete(context, chat_id, msg_id, delay=300):
    """မူလသတ်မှတ်ချက် ၅ မိနစ် (စက္ကန့် ၃၀၀) ဖြင့် ဖျက်မည်"""
    context.job_queue.run_once(auto_delete_job, delay, chat_id=chat_id, data=msg_id)

# ================= 🎛️ Main Menu Keyboard =================
def get_main_menu_keyboard(user_id: int):
    # 👥 အခြေခံ Menu များ (လူတိုင်းမြင်ရမည်)
    keyboard = [
        [
            InlineKeyboardButton("🎓 Academic & Class", callback_data="menu_academic"),
            InlineKeyboardButton("💬 Social & Confess", callback_data="menu_social")
        ],
        [
            InlineKeyboardButton("🛠️ Tools & Utility", callback_data="menu_tools"),
            InlineKeyboardButton("📋 Student Info", callback_data="menu_info") # 👈 Finance ကို ဖြုတ်ပြီး Info သီးသန့်ထားမည်
        ]
    ]
    
    # 📌 Super Admin ဖြစ်မှသာ Finance နှင့် Admin Console ခလုတ်များ ပေါ်မည်
    if is_super_admin(user_id):
        keyboard.append([
            InlineKeyboardButton("💰 Finance & Ledger", callback_data="menu_finance") # 👈 Admin သီးသန့် Finance Menu အသစ်
        ])
        keyboard.append([
            InlineKeyboardButton("📖 Commands Guide", callback_data="menu_examples"),
            InlineKeyboardButton("⚠️ Admin Console", callback_data="menu_admin")
        ])
    else:
        keyboard.append([
            InlineKeyboardButton("📖 Commands Guide", callback_data="menu_examples")
        ])
        
    return InlineKeyboardMarkup(keyboard)

# ================= 🚀 Start System (Boot Animation) =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/start ခေါ်သည့်အခါ Server Connection Animation ဖြင့် Boot တက်မည့်စနစ်"""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    user_msg_id = update.message.message_id
    
    # 🟢 Frame 1
    msg = await update.message.reply_text(
        "<pre>⚙️ OMNI-NET INITIALIZING...\n"
        "┣ 📡 Ping: Requesting...\n"
        "┗ 📦 Data: [▓░░░░░░░░░] 10%</pre>", 
        parse_mode=ParseMode.HTML
    )
    await asyncio.sleep(0.5)
    
    # 🟢 Frame 2
    await msg.edit_text(
        "<pre>⚙️ OMNI-NET INITIALIZING...\n"
        "┣ 📡 Ping: 14ms (Secure)\n"
        "┣ 🗄️ Database: Connected...\n"
        "┗ 📦 Data: [▓▓▓▓▓░░░░░] 50%</pre>", 
        parse_mode=ParseMode.HTML
    )
    await asyncio.sleep(0.5)
    
    # 🟢 Frame 3
    await msg.edit_text(
        "<pre>⚙️ OMNI-NET INITIALIZING...\n"
        "┣ 📡 Ping: 14ms (Secure)\n"
        "┣ 🗄️ Database: Synced\n"
        "┣ 🔐 Security: Bypassed\n"
        "┗ 📦 Data: [▓▓▓▓▓▓▓▓▒░] 85%</pre>", 
        parse_mode=ParseMode.HTML
    )
    await asyncio.sleep(0.5)
    
    # 🟢 Frame 4 (Final Menu)
    main_text = """<blockquote><b>🟢 OMNI-NET TERMINAL V3.1</b></blockquote>
<pre>System Status: Online
Access Level : Granted</pre>

Select Operation Protocol:"""
    
    await msg.edit_text(text=main_text, reply_markup=get_main_menu_keyboard(user_id), parse_mode=ParseMode.HTML)
    
    schedule_delete(context, chat_id, user_msg_id, 300)
    schedule_delete(context, chat_id, msg.message_id, 300)

# ================= 🗂️ Menu Handler =================
async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    
    if query.data == "back_to_main":
        await query.answer()
        main_text = """<blockquote><b>🟢 OMNI-NET TERMINAL V3.1</b></blockquote>
<pre>System Status: Online
Access Level : Granted</pre>

Select Operation Protocol:"""
        await query.edit_message_text(main_text, reply_markup=get_main_menu_keyboard(user_id), parse_mode=ParseMode.HTML)
        return

    back_kb = [[InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_main")]]

    if query.data == "menu_academic":
        await query.answer()
        text = """<blockquote><b>🎓 ACADEMIC & CLASS CORE</b></blockquote>
<pre>📌 အချိန်ဇယား ကြည့်ရန်:
/it, /ec, /civil, /ep, /me, /mc, /archi

📌 တာဝန်နှင့် စာမေးပွဲများ ကြည့်ရန်:
/tasks (သို့) /tutorials

📌 တက်ရောက်သူ စာရင်း (Roll Call):
/rollcall (Attendance PIN ထည့်ရန်)

📌 75% Survival Calculator:
/skip (မိမိပျက်ထားသော အတန်းများကို တွက်ရန်)</pre>"""
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(back_kb), parse_mode=ParseMode.HTML)

    elif query.data == "menu_social":
        await query.answer()
        text = """<blockquote><b>💬 SOCIAL HUB</b></blockquote>
<pre>📌 ရင်ဖွင့် / စနောက်ရန် (DM မှသုံးပါ):
/confess (နာမည်တပ်၍ ပို့ရန်)
/confess1 (Anonymous ပို့ရန်)
/notice (ကြေညာချက်/စနောက်စာ ပို့ရန်)

📌 Profile ပြင်ဆင်ရန်:
/nickname (မိမိအမည်ပြောင် သတ်မှတ်ရန်)</pre>"""
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(back_kb), parse_mode=ParseMode.HTML)

    elif query.data == "menu_tools":
        await query.answer()
        text = """<blockquote><b>🛠️ UTILITY NODE</b></blockquote>
<pre>📌 အထွေထွေ Tool များ:
/weather - ယနေ့ ရာသီဥတု ကြည့်ရန်
/wifi - အတန်းတွင်း WiFi ကြည့်ရန်
/status - ဆာဗာ အခြေအနေ ကြည့်ရန်
/creator - System Info ကြည့်ရန်

📌 လျှို့ဝှက်ကုဒ် Tool များ:
/encrypt &lt;စာသား&gt;
/decrypt &lt;ကုဒ်&gt;
/bin2hex &lt;Binary&gt;
/hex2bin &lt;Hex&gt;</pre>"""
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(back_kb), parse_mode=ParseMode.HTML)

    elif query.data == "menu_info":
        await query.answer()
        text = """<blockquote><b>📋 STUDENT INFO</b></blockquote>
<pre>📌 ကျောင်းသားများ လုပ်ဆောင်ရန်:
/add &lt;RollNo&gt; &lt;Name&gt; - စာရင်းသွင်းရန်
/ph &lt;Phone&gt; - ဖုန်းနံပါတ် ထည့်သွင်းရန်

📌 Admin များ ရှာဖွေရန်:
@bot_name (Inline Search) ဖြင့် 
ခုံနံပါတ်/နာမည် ရိုက်ထည့်ရှာဖွေနိုင်သည်။</pre>"""
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(back_kb), parse_mode=ParseMode.HTML)

    # 💰 [ Admin သီးသန့် Finance Menu ] 💰
    elif query.data == "menu_finance":
        if is_super_admin(user_id):
            await query.answer()
            text = """<blockquote><b>💰 FINANCE & LEDGER (ADMIN ONLY)</b></blockquote>
<pre>📌 ရန်ပုံငွေ ဖန်တီး / ဖျက်သိမ်းရန်:
/newfund &lt;FundID&gt; &lt;TargetAmount&gt; &lt;Manager&gt; [Name]
/delfund &lt;FundID&gt;

📌 ငွေသွင်း / ငွေထုတ် / ရုပ်သိမ်းရန်:
/pay &lt;RollNo&gt; &lt;FundID&gt; &lt;Amount&gt;
/spend &lt;Amount&gt; &lt;Reason&gt;
/undo &lt;RollNo&gt; &lt;FundID&gt;

📌 စာရင်းစစ် / အစီရင်ခံစာများ:
/me - မိမိ၏ EC Dashboard ကြည့်ရန်
/ecstat &lt;@username&gt; - အခြား EC စာရင်းကြည့်ရန်
/vault - ရန်ပုံငွေ အခြေအနေချုပ်
/report &lt;FundID&gt; - Fund အသေးစိတ်ကြည့်ရန်
/export - CSV ဖြင့် စာရင်းထုတ်ရန်
/exportfund &lt;FundID&gt; - Fund အတွက် CSV ထုတ်ရန်

📌 အခြားလုပ်ဆောင်ချက်များ:
/remind &lt;FundID&gt; - ငွေမသွင်းရသေးသူများကို သတိပေးရန်
/archive_fund &lt;FundID&gt; - ဆရာမထံ ငွေအပ်နှံရန်</pre>"""
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(back_kb), parse_mode=ParseMode.HTML)
        else:
            await query.answer("⛔ Access Denied: Level 9 Clearance Required.", show_alert=True)

    elif query.data == "menu_examples":
        await query.answer()
        text = """<blockquote><b>📖 COMMAND EXAMPLES</b></blockquote>
<pre>အောက်ပါ Format များအတိုင်း ကွက်လပ်ခြား၍ ရိုက်ထည့်ပါ -

[ Registration ]
/add Sem-II-CEIT-01 John Doe
/ph 09123456789

[ Tools & Crypto ]
/encrypt I love you
/decrypt Sdsb3ZlIHlvdQ==
/bin2hex 101010

[ Admin Fund Entry ]
/pay 01 sports 5000</pre>"""
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(back_kb), parse_mode=ParseMode.HTML)

    elif query.data == "menu_admin":
        if is_super_admin(user_id):
            await query.answer()
            # Finance Commands များကို ဤနေရာမှ ဖြုတ်လိုက်ပါပြီ
            text = """<blockquote><b>⚠️ ADMIN CONSOLE</b></blockquote>
<pre>📌 အထွေထွေ စီမံခန့်ခွဲမှု:
/list - စာရင်းသွင်းမှု အခြေအနေ
/hostel - အဆောင်နေသူ သီးသန့်ကြည့်ရန်

📌 အတန်းချိန် နှင့် Tasks:
/addclass, /delclass, /clearday
/addtask, /addtutorial
/edittask, /edittutorial

📌 Roll Call စနစ်:
/attendance, /dates

📌 Bot ကို စာသင်ပေးရန်:
/addcmd1, /delcmd

📌 အခြား စနစ်များ:
/addwifi, /delwifi
/addegg, /delegg
/approvers, /setlimit</pre>"""
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(back_kb), parse_mode=ParseMode.HTML)
        else:
            await query.answer("⛔ Access Denied: Level 9 Clearance Required.", show_alert=True)