import logging
import html
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from database.db import get_db

from utils.constants import FOOTER, LOG_GROUP_ID
from utils.helpers import get_safe_user_link, get_allowed_groups

logger = logging.getLogger(__name__)

# ================= 🛡️ DISTRIBUTED ADMIN APPROVAL =================
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
        
        admin_msgs = post.get("admin_msgs", [])
        for adm in admin_msgs:
            chat_id = adm["chat_id"]
            msg_id = adm["msg_id"]
            try:
                if chat_id == update.effective_user.id:
                    await context.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=f"{query.message.text}\n\n<b>✅ {action_text}</b>", parse_mode=ParseMode.HTML)
                else:
                    await context.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=f"{query.message.text}\n\n⚠️ <i>Action taken by {admin_name} ({action_text})</i>", parse_mode=ParseMode.HTML)
            except Exception as e: 
                logger.warning(f"Failed to update admin message (Msg ID: {msg_id}): {e}")

        if LOG_GROUP_ID != 0 and post.get("log_msg_id"):
            try:
                # Approve နှိပ်လိုက်သော Admin ၏ အချက်အလက် (Name Link)
                admin_display = get_safe_user_link(update.effective_user)
                
                # 📌 Database ထဲမှ မူလပို့ခဲ့သော User ၏ Name Link ကို ပြန်ဆွဲထုတ်ခြင်း
                sender_id = post['sender_id']
                sender_uname = post.get('sender_uname', 'Unknown')
                
                # အကယ်၍ posts.py ဘက်တွင် Error ရှိ၍ sender_display မပါလာခဲ့လျှင်တောင် အဆင်ပြေအောင် Fallback ထည့်ပေးထားသည်
                fallback_link = f'<a href="tg://user?id={sender_id}">User Profile</a> (@{sender_uname})'
                sender_display = post.get('sender_display', fallback_link)
                
                send_time = post.get('time', 'Unknown Time')
                sig_line = f"🎭 <b>Signature:</b> {html.escape(post.get('name', ''))}\n" if post['type'] == 'confess_custom' else ""
                
                log_txt = (
                    f"🚨 <b>SYSTEM LOG | #{'Confession' if 'confess' in post['type'] else 'Notice'}</b>\n"
                    f"🎯 <b>Target:</b> {post['target']}\n"
                    f"👤 <b>User:</b> {sender_display} (ID: <code>{sender_id}</code>)\n"
                    f"{sig_line}"
                    f"🕒 <b>Time:</b> {send_time}\n"
                    f"📝 <b>Content:</b>\n<pre>{html.escape(post['msg'])}</pre>\n\n"
                    f"<b>Status:</b> {'✅ APPROVED' if action == 'aprv' else '❌ REJECTED'} by {admin_display}"
                )
                await context.bot.edit_message_text(chat_id=LOG_GROUP_ID, message_id=post["log_msg_id"], text=log_txt, parse_mode=ParseMode.HTML)
            except Exception as e: 
                logger.error(f"Log Update Error for post {post_id}: {e}")

        if action == "aprv":
            target = post['target']
            sender_id = post['sender_id']
            
            allowed_groups = await get_allowed_groups(sender_id, context)
            target_ids = allowed_groups if target == "all" else [int(target)]
            
            if post['type'] == 'confess_anon': 
                final_msg = f"<b>#Confession</b>\n<pre>{html.escape(post['msg'])}</pre>\n\n<i>- Anonymous</i>{FOOTER}"
            elif post['type'] == 'confess_custom': 
                final_msg = f"<b>#Confession</b>\n<pre>{html.escape(post['msg'])}</pre>\n\n<i>- {html.escape(post.get('name', 'Anonymous'))}</i>{FOOTER}"
                
            for gid in target_ids:
                try: 
                    await context.bot.send_message(chat_id=gid, text=final_msg, parse_mode=ParseMode.HTML)
                except Exception as e: 
                    logger.error(f"Failed to send approved broadcast to group {gid}: {e}")
                    
    except Exception as e:
        logger.error(f"Critical error in admin_approval_action: {e}")
        await query.edit_message_text("<pre>❌ System Error Occurred During Approval.</pre>", parse_mode=ParseMode.HTML)