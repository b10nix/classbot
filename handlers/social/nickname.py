import html
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode

from database.db import get_db
from utils.helpers import cancel_conv
from utils.constants import FOOTER, VIP_USERS
from utils.auth import is_super_admin

logger = logging.getLogger(__name__)

RECEIVE_NICKNAME = 1
CONFIRM_NICKNAME = 2

async def nickname_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db = get_db()
    
    user_data = await db.user_settings.find_one({"user_id": user_id})
    
    # 🌟 VIP သို့မဟုတ် Super Admin ဖြစ်လျှင် True ဖြစ်မည်
    is_vip = (user_id in VIP_USERS) or is_super_admin(user_id)
    
    # 📌 1. VIP မဟုတ်တဲ့ သာမန် User တွေကိုသာ တစ်သက်မှာ တစ်ခါပဲ ပြင်ခွင့်ပေးမည် (Lock ချမည်)
    if not is_vip and user_data and user_data.get("nick_changed", False):
        await update.message.reply_text(
            "⛔ <b>Access Denied:</b>\n"
            "မင်းရဲ့ Identity ကို ပြောင်းလဲပြီးသွားပါပြီ။\n"
            "System အရ Identity ကို <b>တစ်သက်မှာ တစ်ခါသာ</b> ပြောင်းလဲခွင့်ပြုထားပါတယ်။",
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END
        
    await update.message.reply_text(
        "🎭 <b>Identity ပြောင်းလဲရန်</b>\n\n"
        "မင်းရဲ့ ကိုယ်ပိုင် Emoji (သို့) Nickname အသစ်ကို ရိုက်ထည့်ပါ။\n"
        "⚠️ <b>သတိပေးချက်:</b> သာမန် User များသည် <b>တစ်သက်လုံးစာအတွက် တစ်ခါသာ</b> ပြောင်းလဲခွင့်ရှိပါသည်။ သေချာစဉ်းစားပြီးမှ ရွေးချယ်ပါ။\n\n"
        "မလုပ်ချင်တော့ပါက /cancel ဟု ရိုက်ပါ။",
        parse_mode=ParseMode.HTML
    )
    return RECEIVE_NICKNAME

async def nickname_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    new_nick = update.message.text.strip()[:15] 
    
    db = get_db()
    
    # 📌 2. Duplicate Check: တခြားသူ ယူထားပြီးသား ဟုတ်/မဟုတ် စစ်ဆေးခြင်း
    existing_user = await db.user_settings.find_one({
        "$or": [{"nickname": new_nick}, {"emoji": new_nick}],
        "user_id": {"$ne": user_id} 
    })
    
    if existing_user:
        await update.message.reply_text(
            f"⚠️ <b>{html.escape(new_nick)}</b> ကို တခြားသူ အသုံးပြုထားပြီးပါပြီ။\n"
            "Unique ဖြစ်ရန်အတွက် အခြား Identity တစ်ခုကို ရိုက်ထည့်ပေးပါ။",
            parse_mode=ParseMode.HTML
        )
        return RECEIVE_NICKNAME
        
    context.user_data['new_nick'] = new_nick
    safe_nick = html.escape(new_nick)
    
    # 📌 3. Format အသစ်ဖြင့် Preview ပြသခြင်း
    preview = f"💬 <b>POST</b>\n\n<pre>ဒါကတော့ Post စာသား နမူနာဖြစ်ပါတယ်။</pre>\n\n- {safe_nick}{FOOTER}"
    
    kb = [
        [InlineKeyboardButton("✅ SAVE NICKNAME", callback_data="savenick_yes"), InlineKeyboardButton("❌ CANCEL", callback_data="savenick_no")],
        [InlineKeyboardButton("🔄 Reset to Random Emoji", callback_data="savenick_reset")]
    ]
    
    is_vip = (user_id in VIP_USERS) or is_super_admin(user_id)
    warning_text = "ဒီအတိုင်း မှတ်ထားလိုက်ရမလားဗျာ?" if is_vip else "ဒီအတိုင်း <b>တစ်သက်လုံးစာ</b> မှတ်ထားလိုက်ရမလားဗျာ?"
    
    await update.message.reply_text(
        f"👀 <b>PREVIEW (နမူနာကြည့်ရန်)</b>\n\n{preview}\n\n{warning_text}",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML
    )
    return CONFIRM_NICKNAME

async def nickname_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    db = get_db()
    user_id = update.effective_user.id
    
    try:
        if query.data == "savenick_yes":
            new_nick = context.user_data.get('new_nick', 'Unknown')
            if db is not None:
                # 📌 4. Save နှိပ်လျှင် nick_changed: True ဟု မှတ်မည် 
                # (VIP များအတွက် True ဖြစ်သွားလည်း အပေါ်က is_vip က ကျော်ပေးသွားမည်)
                await db.user_settings.update_one(
                    {"user_id": user_id}, 
                    {"$set": {
                        "nickname": new_nick,
                        "emoji": new_nick,
                        "nick_changed": True
                    }}, 
                    upsert=True
                )
                
            is_vip = (user_id in VIP_USERS) or is_super_admin(user_id)
            success_note = "" if is_vip else "\n(နောက်ထပ် ပြောင်းလဲခွင့် မရှိတော့ပါ။)"
            
            await query.edit_message_text(f"✅ သင့်ရဲ့ Identity အသစ်ကို <b>{html.escape(new_nick)}</b> အဖြစ် အောင်မြင်စွာ မှတ်သားလိုက်ပါပြီ。{success_note}", parse_mode=ParseMode.HTML)
            
        elif query.data == "savenick_reset":
            if db is not None:
                await db.user_settings.update_one(
                    {"user_id": user_id}, 
                    {"$unset": {"nickname": "", "nick_changed": ""}}
                )
            await query.edit_message_text("✅ ပုံမှန် Random Emoji စနစ်သို့ ပြန်လည်ပြောင်းလဲလိုက်ပါပြီ。", parse_mode=ParseMode.HTML)
            
        else:
            await query.edit_message_text("<pre>🚫 Canceled.</pre>", parse_mode=ParseMode.HTML)
            
    except Exception as e:
        logger.error(f"Error in nickname_action: {e}")
        await query.edit_message_text("<pre>❌ System Error Occurred.</pre>", parse_mode=ParseMode.HTML)
        
    return ConversationHandler.END