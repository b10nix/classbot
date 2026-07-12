import html
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode

from database.db import get_db
from utils.constants import FOOTER
from utils.helpers import get_user_identity, get_allowed_groups

logger = logging.getLogger(__name__)

REPLY_MSG = 1

async def reply_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    # 💡 လာသည့် Deep Link သည် reply_postID_groupID ဖြစ်ရမည်
    if not args or not args[0].startswith("reply_"):
        return ConversationHandler.END
        
    parts = args[0].split("_")
    if len(parts) < 3:
        return ConversationHandler.END
        
    post_id = parts[1]
    group_id = int(parts[2])
    
    db = get_db()
    pub_post = await db.published_posts.find_one({"post_id": post_id})
    if not pub_post:
        await update.message.reply_text("⚠️ ဤ Post အား Database တွင် ရှာမတွေ့တော့ပါ။", parse_mode=ParseMode.HTML)
        return ConversationHandler.END
        
    signature = pub_post.get('signature', 'Unknown')
    
    context.user_data['reply_target_id'] = post_id
    context.user_data['reply_group_id'] = group_id
    context.user_data['reply_signature'] = signature
    
    kb = [[InlineKeyboardButton("❌ Cancel", callback_data="cancel_reply")]]
    
    # 📌 Prompt ကို ပြမည် 
    prompt = await update.message.reply_text(
        f"💬 <b>REPLY SYSTEM</b>\n<pre>Enter your reply message for {signature}:</pre>",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.HTML
    )
    context.user_data['reply_prompt_id'] = prompt.message_id
    return REPLY_MSG

async def reply_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text
    user_id = update.effective_user.id
    
    post_id = context.user_data.get('reply_target_id')
    group_id = context.user_data.get('reply_group_id')
    signature = context.user_data.get('reply_signature', 'Unknown')
    
    # Prompt အဟောင်းအား ဖျက်ခြင်း
    try: await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=context.user_data.get('reply_prompt_id'))
    except Exception: pass
    
    allowed_groups = await get_allowed_groups(user_id, context)
    if group_id not in allowed_groups:
        await update.message.reply_text("⛔ <b>Access Denied:</b> သင်သည် ဤ Group ၏ Member မဟုတ်တော့ပါ။", parse_mode=ParseMode.HTML)
        return ConversationHandler.END
        
    db = get_db()
    pub_post = await db.published_posts.find_one({"post_id": post_id})
    if not pub_post:
        await update.message.reply_text("⚠️ ဤ Post အား Database တွင် ရှာမတွေ့တော့ပါ။", parse_mode=ParseMode.HTML)
        return ConversationHandler.END
        
    # သက်ဆိုင်ရာ Group ၏ Message ID အား ရှာဖွေခြင်း
    target_info = next((t for t in pub_post.get('targets', []) if t['chat_id'] == group_id), None)
    if not target_info:
        await update.message.reply_text("⚠️ ဤ Group အတွက် မူလ Post ကို ရှာမတွေ့ပါ။", parse_mode=ParseMode.HTML)
        return ConversationHandler.END
        
    msg_to_reply = target_info['msg_id']
    identity = await get_user_identity(user_id)
    final_msg = f"💬 <b>REPLY</b> (to {signature})\n\n<pre>{html.escape(msg)}</pre>\n\n- {identity}{FOOTER}"
    
    try:
        # 📌 Native Reply သုံး၍ ပို့ခြင်း
        await context.bot.send_message(
            chat_id=group_id, 
            text=final_msg, 
            reply_to_message_id=msg_to_reply,
            parse_mode=ParseMode.HTML
        )
        await update.message.reply_text("<pre>✅ REPLY DELIVERED SUCCESSFULLY.</pre>", parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Reply send failed for {group_id}: {e}")
        await update.message.reply_text("<pre>❌ Error: Could not deliver reply.</pre>", parse_mode=ParseMode.HTML)
        
    return ConversationHandler.END