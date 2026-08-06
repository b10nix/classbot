import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, CallbackQueryHandler
from telegram.constants import ParseMode
from database.db import get_db

# 📌 Conversation State သတ်မှတ်ခြင်း (အခြား Handler များနှင့် မတိုက်စေရန် 60 ဟုပေးထားသည်)
ADD_HOSTEL = range(60, 61)

# ================= 👤 ကျောင်းသားကိုယ်တိုင် စာရင်းသွင်းခြင်း (/add) =================
async def user_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """အသုံးပြုနည်း: /add <Roll_No> <Name>"""
    chat_type = update.effective_chat.type
    # Group ထဲတွင် သုံးခွင့်မပေးပါ (Chat မရှုပ်စေရန်)
    if chat_type != 'private':
        try: await update.message.delete()
        except: pass
        return

    args = context.args
    if len(args) < 2:
        await update.message.reply_text(
            "⚠️ <b>စာရင်းသွင်းနည်း မှားယွင်းနေပါသည်။</b>\n"
            "အောက်ပါအတိုင်း ကွက်လပ်ခြား၍ ရိုက်ပေးပါ။\n<pre>/add &lt;ခုံနံပါတ်&gt; &lt;နာမည်&gt;</pre>\n"
            "(ဥပမာ - <code>/add Sem-II-CEIT-01 Kyaw Kyaw</code>)", 
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END

    roll_no = args[0]
    name = " ".join(args[1:])
    tg_id = update.effective_user.id

    db = get_db()
    if db is None: return ConversationHandler.END

    # 📌 စစ်ဆေးချက်: ထို Roll No (သို့) TG ID ဖြင့် စာရင်းသွင်းပြီးသား ရှိ/မရှိ စစ်ဆေးခြင်း
    existing_roll = await db.student_info.find_one({"roll_no": roll_no})
    existing_tg = await db.student_info.find_one({"tg_id": tg_id})

    if existing_roll:
        await update.message.reply_text(f"❌ ခုံနံပါတ် <code>{roll_no}</code> သည် စာရင်းသွင်းပြီးသား ဖြစ်နေပါသည်။", parse_mode=ParseMode.HTML)
        return ConversationHandler.END
    if existing_tg:
        await update.message.reply_text(f"❌ သင့်ရဲ့ Telegram အကောင့်သည် ကျောင်းသား <b>{existing_tg.get('name')}</b> အဖြစ် စာရင်းသွင်းထားပြီးသား ဖြစ်နေပါသည်။", parse_mode=ParseMode.HTML)
        return ConversationHandler.END

    # နောက်တစ်ဆင့်အတွက် ယာယီသိမ်းထားခြင်း
    context.user_data['reg_roll'] = roll_no
    context.user_data['reg_name'] = name

    # Inline Button ဖြင့် အဆောင်နေ/မနေ မေးမြန်းခြင်း
    keyboard = [
        [InlineKeyboardButton("🏢 အဆောင်နေသည်", callback_data="hostel_yes")],
        [InlineKeyboardButton("🏠 အပြင်မှ တက်သည်", callback_data="hostel_no")]
    ]
    
    await update.message.reply_text(
        f"🎯 <b>ကျောင်းသား အချက်အလက် စိစစ်ခြင်း</b>\n\n"
        f"👤 နာမည်: <b>{name}</b>\n"
        f"🏷 ခုံနံပါတ်: <code>{roll_no}</code>\n\n"
        f"ကျေးဇူးပြု၍ သင့်ရဲ့ အတန်းတက်ရောက်မှု ပုံစံကို ရွေးချယ်ပါ -",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )
    return ADD_HOSTEL

async def user_add_hostel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inline Button ရွေးချယ်မှုကို လက်ခံပြီး DB ထဲသို့ သိမ်းဆည်းခြင်း"""
    query = update.callback_query
    await query.answer()

    hostel_status = True if query.data == "hostel_yes" else False
    
    roll_no = context.user_data.get('reg_roll')
    name = context.user_data.get('reg_name')
    
    # 📌 Telegram ID နှင့် Username ဖမ်းယူခြင်း
    tg_id = update.effective_user.id
    tg_username = update.effective_user.username 

    # ကျောင်းဝင်နံပါတ် (Student ID) ကို လောလောဆယ် Roll No အတိုင်း ယာယီ Auto ထည့်ပေးထားပါမည်
    student_id = f"TUHmb-{roll_no.split('-')[-1]}" if "-" in roll_no else "TUHmb-Unknown"

    db = get_db()
    if db is not None:
        await db.student_info.insert_one({
            "name": name,
            "roll_no": roll_no,
            "student_id": student_id,
            "tg_id": tg_id,
            "tg_username": tg_username, # 👈 Username ပါ တစ်ခါတည်း သိမ်းပါမည်
            "hostel": hostel_status,
            "phone": "➖",          # ဖုန်းနံပါတ် လောလောဆယ် အလွတ်ထားမည်
            "paid_funds": {}       # ရန်ပုံငွေ အလွတ်ထားမည်
        })

    hostel_text = "✅ အဆောင်နေ" if hostel_status else "❌ အပြင်နေ"
    await query.edit_message_text(
        f"🎉 <b>စာရင်းသွင်းခြင်း အောင်မြင်ပါသည်။</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"👤 နာမည်: {name}\n"
        f"🏷 ခုံနံပါတ်: <code>{roll_no}</code>\n"
        f"🏠 အခြေအနေ: {hostel_text}\n"
        f"💬 Telegram ID: <code>{tg_id}</code>\n\n"
        f"📱 ဆက်လက်ပြီး သင့်ဖုန်းနံပါတ် ထည့်သွင်းရန် <code>/ph &lt;ဖုန်းနံပါတ်&gt;</code> ဟု ပို့ပေးပါဗျာ။",
        parse_mode=ParseMode.HTML
    )
    return ConversationHandler.END


# ================= 📱 ဖုန်းနံပါတ် ကိုယ်တိုင်ထည့်ခြင်း (/ph) =================
async def user_set_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """အသုံးပြုနည်း: /ph <Phone_Number>"""
    chat_type = update.effective_chat.type
    if chat_type != 'private':
        return

    args = context.args
    if len(args) < 1:
        await update.message.reply_text("⚠️ အသုံးပြုနည်း: <code>/ph 09xxxxxxxxx</code> ဟု ရိုက်ထည့်ပေးပါဗျာ။", parse_mode=ParseMode.HTML)
        return

    phone = args[0]
    tg_id = update.effective_user.id

    db = get_db()
    if db is None: return

    # 📌 စစ်ဆေးချက်: အရင်ဆုံး /add ဖြင့် စာရင်းသွင်းထားခြင်း ရှိ/မရှိ စစ်ဆေးခြင်း
    student = await db.student_info.find_one({"tg_id": tg_id})

    if not student:
        await update.message.reply_text(
            "❌ <b>အချက်အလက် မရှိသေးပါ။</b>\n\n"
            "ဖုန်းနံပါတ် မထည့်မီ အရင်ဦးစွာ <code>/add</code> command သုံးပြီး စာရင်းအရင်သွင်းပေးပါရန်။",
            parse_mode=ParseMode.HTML
        )
        return

    # Database တွင် ဖုန်းနံပါတ်အား သွားရောက် Update လုပ်ခြင်း
    await db.student_info.update_one(
        {"_id": student["_id"]},
        {"$set": {"phone": phone}}
    )

    await update.message.reply_text(
        f"✅ <b>ဖုန်းနံပါတ် အောင်မြင်စွာ မှတ်သားပြီးပါပြီ။</b>\n\n"
        f"👤 ကျောင်းသား: {student.get('name')}\n"
        f"📞 ဖုန်းနံပါတ်: <code>{phone}</code>",
        parse_mode=ParseMode.HTML
    )