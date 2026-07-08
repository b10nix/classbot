import html
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode
from database.db import get_db

from utils.helpers import schedule_delete
from utils.states import State

# 📌 ဤဖိုင်အတွက် သီးသန့် Logger
logger = logging.getLogger(__name__)

# ================= 🏷️ NICKNAME SYSTEM (/nickname) =================
async def nickname_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != 'private':
        try: 
            await update.message.delete()
        except Exception as e:
            logger.warning(f"Failed to delete public nickname command: {e}")
            
        warn = await update.message.reply_text("⚠️ DM Access Only.")
        schedule_delete(context, update.effective_chat.id, warn.message_id, 5)
        return ConversationHandler.END
        
    # Import ကို လိုအပ်မှသာ (Local Import) ခေါ်သုံးခြင်းဖြင့် Circular Error ကို ကာကွယ်ခြင်း
    from handlers.social import get_user_identity 
    
    current_id = await get_user_identity(update.effective_user.id)
    await update.message.reply_text(
        f"🏷️ <b>NICKNAME SETUP</b>\n<pre>လက်ရှိသုံးနေသော နာမည်/Emoji: {current_id}</pre>\n\nအသစ်ပြောင်းလိုသော Nickname (သို့မဟုတ်) Emoji ကို ရိုက်ထည့်ပါ။\n(ဥပမာ - Shadow, Batman, 👻)\n\n<i>ပယ်ဖျက်လိုပါက /cancel ကိုနှိပ်ပါ။</i>",
        parse_mode=ParseMode.HTML
    )
    return State.NICK_MSG

async def nickname_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_nick = update.message.text[:20] 
    context.user_data['new_nick'] = new_nick
    safe_nick = html.escape(new_nick)
    
    preview = f"<b>#{safe_nick}</b>\n<pre>ဒါကတော့ Notice စာသား နမူနာဖြစ်ပါတယ်။</pre>\n\n<i>- {safe_nick}</i>"
    kb = [
        [InlineKeyboardButton("✅ SAVE NICKNAME", callback_data="savenick_yes"), InlineKeyboardButton("❌ CANCEL", callback_data="savenick_no")],
        [InlineKeyboardButton("🔄 Reset to Random Emoji", callback_data="savenick_reset")]
    ]
    
    await update.message.reply_text(
        f"👀 <b>PREVIEW (နမူနာကြည့်ရန်)</b>\n\n{preview}\n\nဒီအတိုင်း မှတ်ထားလိုက်ရမလားဗျာ?",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML
    )
    return State.NICK_CONFIRM

async def nickname_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    db = get_db()
    
    try:
        if query.data == "savenick_yes":
            new_nick = context.user_data.get('new_nick', 'Unknown')
            if db is not None:
                await db.user_settings.update_one({"user_id": update.effective_user.id}, {"$set": {"nickname": new_nick}}, upsert=True)
            await query.edit_message_text(f"✅ သင့်ရဲ့ Nickname အသစ်ကို <b>{html.escape(new_nick)}</b> အဖြစ် အောင်မြင်စွာ မှတ်သားလိုက်ပါပြီ。", parse_mode=ParseMode.HTML)
            
        elif query.data == "savenick_reset":
            if db is not None:
                await db.user_settings.update_one({"user_id": update.effective_user.id}, {"$unset": {"nickname": ""}})
            await query.edit_message_text("✅ ပုံမှန် Random Emoji စနစ်သို့ ပြန်လည်ပြောင်းလဲလိုက်ပါပြီ。", parse_mode=ParseMode.HTML)
        else:
            await query.edit_message_text("<pre>🚫 Canceled.</pre>", parse_mode=ParseMode.HTML)
            
    except Exception as e:
        logger.error(f"Error in nickname_action: {e}")
        await query.edit_message_text("<pre>❌ System Error Occurred.</pre>", parse_mode=ParseMode.HTML)
        
    return ConversationHandler.END