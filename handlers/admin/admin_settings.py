import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode
from database.db import get_db
from utils.constants import OWNER_ID, LOG_GROUP_ID, FOOTER
from utils.auth import is_owner
from utils.helpers import get_safe_user_link

logger = logging.getLogger(__name__)

APPROVER_ADD = 7

# ================= ⚙️ ADMIN LIMIT & COOLDOWN SETTINGS =================
async def set_daily_limit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    args = context.args
    if len(args) < 1:
        return await update.message.reply_text("⚠️ အသုံးပြုနည်း: `/setlimit အရေအတွက်`\n(ဥပမာ - `/setlimit 3` [တစ်နေ့ ၃ ခုသာ ခွင့်ပြုရန်])", parse_mode=ParseMode.HTML)
    try:
        val = int(args[0])
        db = get_db()
        if db is not None: await db.settings.update_one({"type": "daily_limit"}, {"$set": {"count": val}}, upsert=True)
        status = f"<b>{val} ခု</b>" if val > 0 else "<b>အကန့်အသတ်မရှိ (Unlimited)</b>"
        await update.message.reply_text(f"✅ တစ်နေ့စာ Confession ပို့ခွင့် အကြိမ်အရေအတွက်ကို {status} သို့ သတ်မှတ်လိုက်ပါပြီ。", parse_mode=ParseMode.HTML)
    except ValueError:
        await update.message.reply_text("❌ ဂဏန်းသာ ထည့်ပါ။")

async def set_cooldown_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    db = get_db()
    setting = await db.settings.find_one({"type": "cooldown_limit"})
    current_limit = setting.get("minutes", 0) if setting else 0
    keyboard = [
        [InlineKeyboardButton("🚫 ပိတ်မည် (Off)", callback_data="setcd_0")],
        [InlineKeyboardButton("⏳ 5 Mins", callback_data="setcd_5"), InlineKeyboardButton("⏳ 15 Mins", callback_data="setcd_15"), InlineKeyboardButton("⏳ 30 Mins", callback_data="setcd_30")],
        [InlineKeyboardButton("⏳ 1 Hour", callback_data="setcd_60"), InlineKeyboardButton("⏳ 3 Hours", callback_data="setcd_180"), InlineKeyboardButton("⏳ 6 Hours", callback_data="setcd_360")]
    ]
    await update.message.reply_text(f"⚙️ <b>GLOBAL COOLDOWN SETTINGS</b>\n\nလက်ရှိ: <b>{current_limit} မိနစ်</b>\nအသစ်သတ်မှတ်ရန် အောက်တွင်ရွေးချယ်ပါ:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)

async def set_cooldown_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query.data.startswith("setcd_"): return
    await query.answer()
    try:
        if not is_owner(update.effective_user.id): return await query.edit_message_text("⚠️ Admin Only.", parse_mode=ParseMode.HTML)
        val = int(query.data.split('_')[1])
        db = get_db()
        if db is not None: await db.settings.update_one({"type": "cooldown_limit"}, {"$set": {"minutes": val}}, upsert=True)
        status = f"<b>{val} မိနစ်</b>" if val > 0 else "<b>ပိတ်ထားသည် (Off)</b>"
        await query.edit_message_text(f"✅ Global Cooldown အချိန်ကို {status} သို့ ပြောင်းလဲသတ်မှတ်လိုက်ပါပြီ。", parse_mode=ParseMode.HTML)
    except Exception as e:
        await query.edit_message_text(f"❌ Error:\n<pre>{e}</pre>", parse_mode=ParseMode.HTML)

async def set_user_cooldown_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    args = context.args
    if len(args) < 2: return await update.message.reply_text("⚠️ အသုံးပြုနည်း: `/setusercd <User_ID> <Minutes>`", parse_mode=ParseMode.HTML)
    try:
        target_id = int(args[0])
        val = int(args[1])
        db = get_db()
        if db is not None: await db.user_settings.update_one({"user_id": target_id}, {"$set": {"custom_cooldown": val}}, upsert=True)
        status = f"<b>{val} မိနစ်</b>" if val > 0 else "<b>Global ပုံမှန်တိုင်း</b>"
        await update.message.reply_text(f"👤 User <code>{target_id}</code> အတွက် Cooldown ကို {status} အဖြစ် သတ်မှတ်လိုက်ပါပြီ。", parse_mode=ParseMode.HTML)
    except ValueError:
        await update.message.reply_text("❌ User ID နှင့် မိနစ်ကို ဂဏန်းဖြင့်သာ ထည့်ပါ။")

# ================= ⚙️ APPROVERS MANAGEMENT =================
async def approvers_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    db = get_db()
    approvers = await db.approvers.find({}).to_list(length=None) if db is not None else []
    kb = []
    for app in approvers: kb.append([InlineKeyboardButton(f"❌ Remove: {app['name']}", callback_data=f"delapp_{app['user_id']}")])
    kb.append([InlineKeyboardButton("➕ Add New Approver", callback_data="addapp_start")])
    kb.append([InlineKeyboardButton("Done", callback_data="cancel_app")])
    await update.message.reply_text("👮‍♂️ <b>APPROVER MANAGEMENT</b>\n<pre>Manage admins who can approve confessions:</pre>", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)

async def approvers_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    if not is_owner(update.effective_user.id): return await query.answer("⚠️ Owner Only")
    db = get_db()
    
    if data == "cancel_app":
        await query.edit_message_text("<pre>✅ Approver management closed.</pre>", parse_mode=ParseMode.HTML)
        return ConversationHandler.END
        
    if data.startswith("delapp_"):
        uid = int(data.split("_")[1])
        if db is not None: await db.approvers.delete_one({"user_id": uid})
        await query.answer("Removed!")
        approvers = await db.approvers.find({}).to_list(length=None) if db is not None else []
        kb = []
        for app in approvers: kb.append([InlineKeyboardButton(f"❌ Remove: {app['name']}", callback_data=f"delapp_{app['user_id']}")])
        kb.append([InlineKeyboardButton("➕ Add New Approver", callback_data="addapp_start")])
        kb.append([InlineKeyboardButton("Done", callback_data="cancel_app")])
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(kb))
        return ConversationHandler.END

    if data == "addapp_start":
        await query.answer()
        await query.edit_message_text("<b>➕ ADD APPROVER</b>\n<pre>Enter the Telegram User ID and Name.\nFormat: ID Name\nExample: 12345678 John</pre>", parse_mode=ParseMode.HTML)
        return APPROVER_ADD

async def add_approver_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().split(maxsplit=1)
    if len(text) < 2:
        await update.message.reply_text("❌ Format အမှား။ ဥပမာ: `12345678 John`", parse_mode=ParseMode.HTML)
        return APPROVER_ADD
    try:
        uid = int(text[0])
        name = text[1]
        db = get_db()
        if db is not None: await db.approvers.update_one({"user_id": uid}, {"$set": {"name": name}}, upsert=True)
        await update.message.reply_text(f"✅ {name} ({uid}) အား Approver အဖြစ် သတ်မှတ်လိုက်ပါပြီ。\n/approvers ကိုနှိပ်၍ ပြန်စစ်ဆေးနိုင်ပါသည်။")
    except ValueError:
        await update.message.reply_text("❌ User ID သည် ဂဏန်းသာဖြစ်ရမည်။")
    return ConversationHandler.END