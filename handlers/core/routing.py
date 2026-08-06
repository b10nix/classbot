import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from database.db import get_db

logger = logging.getLogger(__name__)
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
MAJORS = ["it", "mc", "archi", "ep", "ec", "me", "civil"]

def get_class_groups():
    group_id_str = os.getenv("CLASS_GROUP_ID", "0")
    try:
        return [int(g.strip()) for g in group_id_str.split(",") if g.strip() and g.strip() != "0"]
    except ValueError:
        return []

async def route_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: 
        return await update.message.reply_text("⚠️ Admin Only")
        
    groups = get_class_groups()
    kb = []
    
    msg = await update.message.reply_text("🔄 Data ဆွဲယူနေပါသည်...")
    
    for gid in groups:
        try:
            chat = await context.bot.get_chat(gid)
            title = chat.title[:30] + "..." if len(chat.title) > 30 else chat.title
        except:
            title = f"Group ID: {gid}"
        kb.append([InlineKeyboardButton(f"⚙️ {title}", callback_data=f"route_grp_{gid}")])
        
    kb.append([InlineKeyboardButton("❌ Cancel", callback_data="route_cancel")])
    await msg.edit_text("🎛 <b>TIMETABLE ROUTING SYSTEM</b>\n\nမည်သည့် Group ၏ အချိန်ဇယား ပို့လွှတ်မှုကို ပြင်ဆင်မည်နည်း?", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)

async def route_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    if update.effective_user.id != OWNER_ID: return await query.answer("Admin Only")
    await query.answer()

    if data == "route_cancel":
        return await query.edit_message_text("✅ Routing setup closed.")

    db = get_db()
    
    if data.startswith("route_grp_"):
        gid = int(data.split("_")[2])
        doc = await db.group_routes.find_one({"group_id": gid}) if db is not None else None
        # Default အနေဖြင့် Database တွင် မရှိသေးပါက Major အကုန်လုံးကို ပွင့်နေသည်ဟု မှတ်ယူပါမည်
        allowed = doc.get("majors", MAJORS) if doc else MAJORS

        kb = []
        for m in MAJORS:
            status = "✅" if m in allowed else "❌"
            kb.append([InlineKeyboardButton(f"{status} {m.upper()}", callback_data=f"route_tog_{gid}_{m}")])
        kb.append([InlineKeyboardButton("🔙 Back to Groups", callback_data="route_back")])

        try:
            chat = await context.bot.get_chat(gid)
            title = chat.title
        except:
            title = str(gid)

        await query.edit_message_text(f"🎛 <b>ROUTING FOR:</b> {title}\n\nအောက်ပါ Major များကို အဖွင့်/အပိတ် (Toggle) လုပ်နိုင်ပါသည်။", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)

    elif data.startswith("route_tog_"):
        parts = data.split("_")
        gid = int(parts[2])
        major = parts[3]

        doc = await db.group_routes.find_one({"group_id": gid}) if db is not None else None
        allowed = doc.get("majors", MAJORS) if doc else MAJORS

        # Toggle Logic: ရှိရင် ဖယ်မည်၊ မရှိရင် ထည့်မည်
        if major in allowed:
            allowed.remove(major)
        else:
            allowed.append(major)

        if db is not None:
            await db.group_routes.update_one({"group_id": gid}, {"$set": {"majors": allowed}}, upsert=True)

        # Refresh Keyboard
        kb = []
        for m in MAJORS:
            status = "✅" if m in allowed else "❌"
            kb.append([InlineKeyboardButton(f"{status} {m.upper()}", callback_data=f"route_tog_{gid}_{m}")])
        kb.append([InlineKeyboardButton("🔙 Back to Groups", callback_data="route_back")])
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(kb))

    elif data == "route_back":
        groups = get_class_groups()
        kb = []
        for gid in groups:
            try:
                chat = await context.bot.get_chat(gid)
                title = chat.title[:30] + "..." if len(chat.title) > 30 else chat.title
            except:
                title = f"Group ID: {gid}"
            kb.append([InlineKeyboardButton(f"⚙️ {title}", callback_data=f"route_grp_{gid}")])
        kb.append([InlineKeyboardButton("❌ Cancel", callback_data="route_cancel")])
        await query.edit_message_text("🎛 <b>TIMETABLE ROUTING SYSTEM</b>\n\nမည်သည့် Group ၏ အချိန်ဇယား ပို့လွှတ်မှုကို ပြင်ဆင်မည်နည်း?", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)