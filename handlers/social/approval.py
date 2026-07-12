import logging
import html
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from database.db import get_db

from utils.constants import FOOTER, LOG_GROUP_ID
from utils.helpers import get_safe_user_link, get_allowed_groups, get_user_identity

logger = logging.getLogger(__name__)

async def admin_approval_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not (query.data.startswith("aprv_") or query.data.startswith("rjct_")): return
    await query.answer()
    
    action, post_id = query.data.split("_")
    db = get_db()
    if db is None: return
    
    try:
        post = await db.pending_posts.find_one_and_delete({"post_id": post_id})
        if not post:
            return await query.edit_message_text(f"{query.message.text}\n\n⚠️ <i>ဤစာအား အခြား Admin တစ်ဦးမှ စိစစ်ပြီးသွားပါပြီ။</i>", parse_mode=ParseMode.HTML)
            
        admin_name = update.effective_user.first_name
        action_text = "APPROVED" if action == "aprv" else "REJECTED"
        sender_id = post['sender_id']
        
        # 📌 1. Admin များကို အသိပေးခြင်း
        admin_msgs = post.get("admin_msgs", [])
        for adm in admin_msgs:
            chat_id = adm["chat_id"]
            msg_id = adm["msg_id"]
            try:
                if chat_id == update.effective_user.id:
                    await context.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=f"{query.message.text}\n\n<b>✅ {action_text}</b>", parse_mode=ParseMode.HTML)
                else:
                    await context.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=f"{query.message.text}\n\n⚠️ <i>Action taken by {admin_name} ({action_text})</i>", parse_mode=ParseMode.HTML)
            except Exception as e: pass

        # 📌 2. Master Log မှတ်တမ်းတင်ခြင်း
        if LOG_GROUP_ID != 0 and post.get("log_msg_id"):
            try:
                admin_display = get_safe_user_link(update.effective_user)
                sender_uname = post.get('sender_uname', 'Unknown')
                fallback_link = f'<a href="tg://user?id={sender_id}">User Profile</a> (@{sender_uname})'
                sender_display = post.get('sender_display', fallback_link)
                
                send_time = post.get('time', 'Unknown Time')
                sig_line = f"🎭 <b>Signature:</b> {html.escape(post.get('name', ''))}\n" if post['type'] == 'confess_custom' else ""
                
                log_txt = (
                    f"🚨 <b>SYSTEM LOG | #{'Confession' if 'confess' in post['type'] else 'Post'}</b>\n"
                    f"🎯 <b>Target:</b> {post['target']}\n"
                    f"👤 <b>User:</b> {sender_display} (ID: <code>{sender_id}</code>)\n"
                    f"{sig_line}"
                    f"🕒 <b>Time:</b> {send_time}\n"
                    f"📝 <b>Content:</b>\n<pre>{html.escape(post['msg'])}</pre>\n\n"
                    f"<b>Status:</b> {'✅ APPROVED' if action == 'aprv' else '❌ REJECTED'} by {admin_display}"
                )
                await context.bot.edit_message_text(chat_id=LOG_GROUP_ID, message_id=post["log_msg_id"], text=log_txt, parse_mode=ParseMode.HTML)
            except Exception: pass

        # 📌 3. REJECT လုပ်ခံရပါက User ထံ သတိပေးခြင်း
        if action == "rjct":
            try:
                reject_msg = (
                    "❌ <b>Admin မှ သင်၏စာကို Reject လုပ်လိုက်ပါသည်။</b>\n\n"
                    "ဤစာသည် Confession (ရင်ဖွင့်စာ) အမျိုးအစားနှင့် မသက်ဆိုင်ဟု ယူဆပါသဖြင့် ပယ်ချခံရခြင်း ဖြစ်နိုင်ပါသည်။\n"
                    "သာမန်စာသားများ၊ မေးခွန်းများ ဖြစ်ပါက /post ကို ပြောင်းလဲအသုံးပြုပေးပါ။"
                )
                await context.bot.send_message(chat_id=sender_id, text=reject_msg, parse_mode=ParseMode.HTML)
            except Exception: pass

        # 📌 4. APPROVE လုပ်ပါက Group သို့ ပို့ဆောင်ခြင်း (Database တွင် မှတ်တမ်းတင်ခြင်း ပါဝင်သည်)
        if action == "aprv":
            target = post['target']
            allowed_groups = await get_allowed_groups(sender_id, context)
            target_ids = allowed_groups if target == "all" else [int(target)]
            sender_emoji = await get_user_identity(sender_id)
            
            # Format နှင့် Signature ခွဲခြားခြင်း
            if post['type'] == 'confess_anon':
                signature = "Anonymous"
                final_msg = f"<b>#Confession</b>\n<pre>{html.escape(post['msg'])}</pre>\n\n- Anonymous{FOOTER}"
            elif post['type'] == 'confess_custom':
                signature = html.escape(post.get('name', 'Anonymous'))
                final_msg = f"<b>#Confession</b>\n<pre>{html.escape(post['msg'])}</pre>\n\n- {signature}{FOOTER}"
            else:
                signature = sender_emoji
                final_msg = f"💬 <b>POST</b>\n\n<pre>{html.escape(post['msg'])}</pre>\n\n- {signature}{FOOTER}"
            
            bot_username = context.bot.username
            published_targets = []
            
            for gid in target_ids:
                try: 
                    # 💡 Group ID ကိုပါ Deep Link တွင် တွဲထည့်ထားသည်
                    reply_markup = InlineKeyboardMarkup([[
                        InlineKeyboardButton(f"💬 Reply to {signature}", url=f"https://t.me/{bot_username}?start=reply_{post_id}_{gid}")
                    ]])
                    sent_msg = await context.bot.send_message(chat_id=gid, text=final_msg, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
                    published_targets.append({"chat_id": gid, "msg_id": sent_msg.message_id})
                except Exception as e: 
                    logger.error(f"Failed to send approved broadcast to group {gid}: {e}")
                    
            # 📌 5. မည်သည့် Group တွင် မည်သည့် Message ID ဖြင့် တင်ခဲ့ကြောင်း Database သို့ သိမ်းဆည်းခြင်း
            if published_targets:
                await db.published_posts.insert_one({
                    "post_id": post_id,
                    "signature": signature,
                    "targets": published_targets
                })
                    
    except Exception as e:
        logger.error(f"Critical error in admin_approval_action: {e}")
        await query.edit_message_text("<pre>❌ System Error Occurred During Approval.</pre>", parse_mode=ParseMode.HTML)