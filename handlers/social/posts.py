import uuid
import logging
import asyncio
import html
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode
from database.db import get_db

from utils.constants import OWNER_ID, LOG_GROUP_ID
from utils.states import State
from utils.helpers import get_mm_time, schedule_delete, build_target_keyboard, get_user_identity, get_safe_user_link, get_allowed_groups
from utils.security import check_daily_limit, increment_daily_limit, check_user_cooldown, update_user_cooldown, show_cooldown_warning

logger = logging.getLogger(__name__)

# ================= ANONYMOUS CONFESSION (/confess1) =================
async def confess_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != 'private':
        try: 
            await update.message.delete()
        except Exception as e: 
            logger.warning(f"Message delete failed in confess_start: {e}")
            
        warn = await update.message.reply_text("⚠️ DM Access Only.")
        schedule_delete(context, update.effective_chat.id, warn.message_id, 5)
        return ConversationHandler.END
        
    daily_ok, limit_count = await check_daily_limit(update.effective_user.id)
    if not daily_ok:
        warn = await update.message.reply_text(f"🚫 <b>DAILY LIMIT REACHED</b>\n\nယနေ့အတွက် သတ်မှတ်ထားသော ကန့်သတ်ချက် (<b>{limit_count}</b> ခု) ပြည့်သွားပါပြီ。", parse_mode=ParseMode.HTML)
        schedule_delete(context, update.effective_chat.id, warn.message_id, 15)
        return ConversationHandler.END
        
    remain_sec = await check_user_cooldown(update.effective_user.id)
    if remain_sec > 0:
        await show_cooldown_warning(update, remain_sec, context)
        return ConversationHandler.END

    await update.message.reply_text("<b>🕵️ ANONYMOUS CONFESSION</b>\n<pre>Enter your message (Identity Hidden):</pre>", parse_mode=ParseMode.HTML)
    return State.CONFESS_MSG

async def confess_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['msg'] = update.message.text
    context.user_data['sender_uname'] = update.effective_user.username or "No_Username"
    
    markup = await build_target_keyboard(update.effective_user.id, context, "send_confess", "cancel_confess")
    await update.message.reply_text(f"<b>📝 VERIFY & SELECT TARGET</b>\n<pre>{html.escape(update.message.text)}</pre>", reply_markup=markup, parse_mode=ParseMode.HTML)
    return State.CONFESS_CONFIRM

async def confess_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    try:
        if query.data.startswith("send_confess"):
            chat_id = query.message.chat_id
            msg_id = query.message.message_id
            user_id = update.effective_user.id
            
            await context.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text="<pre>🤖 သင်၏စာကို AI မှ အကဲဖြတ်နေပါသည်... [■■□□□]</pre>", parse_mode=ParseMode.HTML)
            await asyncio.sleep(0.8)
            await context.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text="<pre>🤖 သင်၏စာကို AI မှ အကဲဖြတ်နေပါသည်... [■■■■■]</pre>", parse_mode=ParseMode.HTML)
            await asyncio.sleep(0.6)
            
            target = query.data.replace("send_confess_", "")
            msg = context.user_data.get('msg', '')
            post_id = str(uuid.uuid4())[:8]
            db = get_db()
            log_msg_id = None
            admin_msgs = []
            
            if db is not None:
                display_user = get_safe_user_link(update.effective_user) 
                
                if LOG_GROUP_ID != 0:
                    log_txt = (
                        f"🚨 <b>SYSTEM LOG | #Confession (Anonymous)</b>\n"
                        f"🎯 <b>Target:</b> {target}\n"
                        f"👤 <b>User:</b> {display_user} (ID: <code>{user_id}</code>)\n"
                        f"🕒 <b>Time:</b> {get_mm_time()}\n"
                        f"📝 <b>Content:</b>\n<pre>{html.escape(msg)}</pre>\n\n"
                        f"⏳ <i>Pending Approval...</i>"
                    )
                    try: 
                        m = await context.bot.send_message(LOG_GROUP_ID, text=log_txt, parse_mode=ParseMode.HTML)
                        log_msg_id = m.message_id
                    except Exception as e: 
                        logger.warning(f"Failed to send log to LOG_GROUP: {e}")
                
                approvers = await db.approvers.find({}).to_list(length=None)
                approver_ids = [a['user_id'] for a in approvers]
                if OWNER_ID not in approver_ids: approver_ids.append(OWNER_ID)
                
                dm_txt = f"🚨 <b>PENDING CONFESSION</b>\n👤 <b>Telegram ID:</b> <code>{user_id}</code>\n🎯 <b>Target:</b> {target}\n\n📝 <b>Message:</b>\n<pre>{html.escape(msg)}</pre>"
                kb = [[InlineKeyboardButton("✅ APPROVE", callback_data=f"aprv_{post_id}"), InlineKeyboardButton("❌ REJECT", callback_data=f"rjct_{post_id}")]]
                
                for adm_id in approver_ids:
                    is_allowed = False
                    if adm_id == OWNER_ID or target == "all":
                        is_allowed = True
                    else:
                        try:
                            member = await context.bot.get_chat_member(chat_id=int(target), user_id=adm_id)
                            if member.status in ['member', 'administrator', 'creator', 'restricted']:
                                is_allowed = True
                        except Exception: 
                            pass
                    
                    if is_allowed:
                        try:
                            m = await context.bot.send_message(adm_id, text=dm_txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
                            admin_msgs.append({"chat_id": adm_id, "msg_id": m.message_id})
                        except Exception as e: 
                            logger.warning(f"Failed to send approval DM to admin {adm_id}: {e}")
                    
                await db.pending_posts.insert_one({
                    "post_id": post_id, "type": "confess_anon", "msg": msg, "target": target,
                    "sender_id": user_id, "sender_uname": context.user_data.get('sender_uname', ''), "sender_display": display_user, "time": get_mm_time(),
                    "admin_msgs": admin_msgs, "log_msg_id": log_msg_id
                })
                    
            await update_user_cooldown(user_id)
            await increment_daily_limit(user_id)
            await context.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text="<pre>✅ AI အကဲဖြတ်မှု အောင်မြင်ပါသည်။ Admin ထံသို့ ပို့ပြီးပါပြီ。</pre>", parse_mode=ParseMode.HTML)
        else:
            await query.edit_message_text("<pre>🚫 ABORTED.</pre>", parse_mode=ParseMode.HTML)
            
    except Exception as e:
        logger.error(f"Error in confess_action: {e}")
        await query.edit_message_text("<pre>❌ System Error Occurred.</pre>", parse_mode=ParseMode.HTML)
        
    return ConversationHandler.END

# ================= CUSTOM CONFESSION (/confess) =================
async def c1_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != 'private':
        try: await update.message.delete()
        except Exception: pass
        warn = await update.message.reply_text("⚠️ DM Access Only.")
        schedule_delete(context, update.effective_chat.id, warn.message_id, 5)
        return ConversationHandler.END
        
    daily_ok, limit_count = await check_daily_limit(update.effective_user.id)
    if not daily_ok:
        warn = await update.message.reply_text(f"🚫 <b>DAILY LIMIT REACHED</b>\n\nယနေ့အတွက် သတ်မှတ်ထားသော ကန့်သတ်ချက် (<b>{limit_count}</b> ခု) ပြည့်သွားပါပြီ。", parse_mode=ParseMode.HTML)
        schedule_delete(context, update.effective_chat.id, warn.message_id, 15)
        return ConversationHandler.END
        
    remain_sec = await check_user_cooldown(update.effective_user.id)
    if remain_sec > 0:
        await show_cooldown_warning(update, remain_sec, context)
        return ConversationHandler.END

    await update.message.reply_text("<b>🎭 CONFESSION SYSTEM (NAME REQUIRED)</b>\n<pre>Enter your confession message:</pre>", parse_mode=ParseMode.HTML)
    return State.C1_MSG

async def c1_receive_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['c1_msg'] = update.message.text
    await update.message.reply_text("<b>✍️ SIGNATURE</b>\n<pre>Enter the name/signature you want to show (e.g. Ben):</pre>", parse_mode=ParseMode.HTML)
    return State.C1_NAME

async def c1_receive_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['c1_name'] = update.message.text
    context.user_data['sender_uname'] = update.effective_user.username or "No_Username"
    
    msg = context.user_data['c1_msg']
    name = context.user_data['c1_name']
    
    markup = await build_target_keyboard(update.effective_user.id, context, "send_c1", "cancel_c1")
    await update.message.reply_text(f"<b>📝 PREVIEW & SELECT TARGET</b>\n<pre>{html.escape(msg)}</pre>\n\n<i>- {html.escape(name)}</i>", reply_markup=markup, parse_mode=ParseMode.HTML)
    return State.C1_CONFIRM

async def c1_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    try:
        if query.data.startswith("send_c1"):
            chat_id = query.message.chat_id
            msg_id = query.message.message_id
            user_id = update.effective_user.id
            
            await context.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text="<pre>🤖 သင်၏စာကို AI မှ အကဲဖြတ်နေပါသည်... [■■□□□]</pre>", parse_mode=ParseMode.HTML)
            await asyncio.sleep(0.8)
            await context.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text="<pre>🤖 သင်၏စာကို AI မှ အကဲဖြတ်နေပါသည်... [■■■■■]</pre>", parse_mode=ParseMode.HTML)
            await asyncio.sleep(0.6)
            
            target = query.data.replace("send_c1_", "")
            msg = context.user_data.get('c1_msg', '')
            name = context.user_data.get('c1_name', 'Anonymous')
            post_id = str(uuid.uuid4())[:8]
            db = get_db()
            log_msg_id = None
            admin_msgs = []
            
            if db is not None:
                display_user = get_safe_user_link(update.effective_user)
                if LOG_GROUP_ID != 0:
                    display_user = get_safe_user_link(update.effective_user)
                    log_txt = (
                        f"🚨 <b>SYSTEM LOG | #Confession (Named)</b>\n"
                        f"🎯 <b>Target:</b> {target}\n"
                        f"👤 <b>User:</b> {display_user} (ID: <code>{user_id}</code>)\n"
                        f"🎭 <b>Signature:</b> {html.escape(name)}\n"
                        f"🕒 <b>Time:</b> {get_mm_time()}\n"
                        f"📝 <b>Content:</b>\n<pre>{html.escape(msg)}</pre>\n\n"
                        f"⏳ <i>Pending Approval...</i>"
                    )
                    try: 
                        m = await context.bot.send_message(LOG_GROUP_ID, text=log_txt, parse_mode=ParseMode.HTML)
                        log_msg_id = m.message_id
                    except Exception: pass
                
                approvers = await db.approvers.find({}).to_list(length=None)
                approver_ids = [a['user_id'] for a in approvers]
                if OWNER_ID not in approver_ids: approver_ids.append(OWNER_ID)
                
                dm_txt = f"🚨 <b>PENDING CONFESSION (CUSTOM)</b>\n👤 <b>Telegram ID:</b> <code>{user_id}</code>\n🎯 <b>Target:</b> {target}\n🎭 <b>Signature:</b> {html.escape(name)}\n\n📝 <b>Message:</b>\n<pre>{html.escape(msg)}</pre>"
                kb = [[InlineKeyboardButton("✅ APPROVE", callback_data=f"aprv_{post_id}"), InlineKeyboardButton("❌ REJECT", callback_data=f"rjct_{post_id}")]]
                
                for adm_id in approver_ids:
                    is_allowed = False
                    if adm_id == OWNER_ID or target == "all":
                        is_allowed = True
                    else:
                        try:
                            member = await context.bot.get_chat_member(chat_id=int(target), user_id=adm_id)
                            if member.status in ['member', 'administrator', 'creator', 'restricted']:
                                is_allowed = True
                        except Exception: pass
                    
                    if is_allowed:
                        try:
                            m = await context.bot.send_message(adm_id, text=dm_txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
                            admin_msgs.append({"chat_id": adm_id, "msg_id": m.message_id})
                        except Exception: pass
                    
                await db.pending_posts.insert_one({
                    "post_id": post_id, "type": "confess_custom", "msg": msg, "target": target, "name": name,
                    "sender_id": user_id, "sender_uname": context.user_data.get('sender_uname', ''), "sender_display": display_user, "time": get_mm_time(),
                    "admin_msgs": admin_msgs, "log_msg_id": log_msg_id
                })
                    
            await update_user_cooldown(user_id)
            await increment_daily_limit(user_id)
            await context.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text="<pre>✅ AI အကဲဖြတ်မှု အောင်မြင်ပါသည်။ Admin ထံသို့ ပို့ပြီးပါပြီ。</pre>", parse_mode=ParseMode.HTML)
        else:
            await query.edit_message_text("<pre>🚫 ABORTED.</pre>", parse_mode=ParseMode.HTML)
            
    except Exception as e:
        logger.error(f"Error in c1_action: {e}")
        await query.edit_message_text("<pre>❌ System Error Occurred.</pre>", parse_mode=ParseMode.HTML)
        
    return ConversationHandler.END

# ================= NOTICE SYSTEM (/notice) [DIRECT SEND] =================
async def notice_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != 'private':
        try: await update.message.delete()
        except Exception: pass
        warn = await update.message.reply_text("<pre>⚠️ DM Access Only.</pre>", parse_mode=ParseMode.HTML)
        schedule_delete(context, update.effective_chat.id, warn.message_id, 5)
        return ConversationHandler.END
        
    remain_sec = await check_user_cooldown(update.effective_user.id)
    if remain_sec > 0:
        await show_cooldown_warning(update, remain_sec, context)
        return ConversationHandler.END

    await update.message.reply_text("<b>📢 SECURE NOTICE SYSTEM</b>\n<pre>Enter your notice message:</pre>", parse_mode=ParseMode.HTML)
    return State.NOTICE_MSG

async def notice_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['notice_msg'] = update.message.text
    context.user_data['sender_uname'] = update.effective_user.username or "No_Username"

    markup = await build_target_keyboard(update.effective_user.id, context, "send_notice", "cancel_notice")
    await update.message.reply_text(
        f"<b>📝 VERIFY & SELECT TARGET</b>\n<pre>{html.escape(update.message.text)}</pre>",
        reply_markup=markup, parse_mode=ParseMode.HTML
    )
    return State.NOTICE_CONFIRM

async def notice_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    try:
        if query.data.startswith("send_notice"):
            target = query.data.replace("send_notice_", "")
            
            allowed_groups = await get_allowed_groups(update.effective_user.id, context)
            target_ids = allowed_groups if target == "all" else [int(target)]
            msg = context.user_data.get('notice_msg', '')
            
            identity = await get_user_identity(update.effective_user.id)
            final_msg = f"<b>#{identity}</b>\n<pre>{html.escape(msg)}</pre>\n\n<i>- {identity}</i>"
            
            for gid in target_ids:
                try: 
                    await context.bot.send_message(chat_id=gid, text=final_msg, parse_mode=ParseMode.HTML)
                except Exception as e: 
                    logger.error(f"Group {gid} သို့ပို့ရန်အဆင်မပြေပါ: {e}")
            
            if LOG_GROUP_ID != 0:
                display_user = get_safe_user_link(update.effective_user)
                log_txt = (
                    f"🚨 <b>SYSTEM LOG | #Notice (Direct)</b>\n"
                    f"🎯 <b>Target:</b> {target}\n"
                    f"👤 <b>User:</b> {display_user} (ID: <code>{update.effective_user.id}</code>)\n"
                    f"🏷️ <b>Identity:</b> {identity}\n"
                    f"🕒 <b>Time:</b> {get_mm_time()}\n"
                    f"📝 <b>Content:</b>\n<pre>{html.escape(msg)}</pre>"
                )
                try: 
                    await context.bot.send_message(LOG_GROUP_ID, text=log_txt, parse_mode=ParseMode.HTML)
                except Exception as e: 
                    logger.error(f"Failed to send Notice log: {e}")
                
            await update_user_cooldown(update.effective_user.id)
            await query.edit_message_text("<pre>✅ NOTICE DELIVERED.</pre>", parse_mode=ParseMode.HTML)
        else:
            await query.edit_message_text("<pre>🚫 ABORTED.</pre>", parse_mode=ParseMode.HTML)
            
    except Exception as e:
        logger.error(f"Error in notice_action: {e}")
        await query.edit_message_text(f"<pre>❌ Error: System Issue</pre>", parse_mode=ParseMode.HTML)
        
    return ConversationHandler.END

# ================= 👻 HIDDEN FEATURE: GHOST NOTICE (/notice999) =================
async def notice999_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm မလိုဘဲ စာသားကို Group အားလုံးထံ ချက်ချင်း ပို့ပေးမည့် လျှို့ဝှက် Command"""
    if update.effective_chat.type != 'private':
        try: await update.message.delete()
        except Exception: pass
        warn = await update.message.reply_text("<pre>⚠️ DM Access Only.</pre>", parse_mode=ParseMode.HTML)
        schedule_delete(context, update.effective_chat.id, warn.message_id, 5)
        return

    remain_sec = await check_user_cooldown(update.effective_user.id)
    if remain_sec > 0:
        await show_cooldown_warning(update, remain_sec, context)
        return

    text = update.message.text
    parts = text.split(maxsplit=1)
    
    if len(parts) < 2:
        await update.message.reply_text(
            "⚠️ <b>အသုံးပြုနည်း မှားယွင်းနေပါသည်။</b>\n<pre>/notice999 &lt;စာသား&gt;</pre>\n(ဥပမာ - `/notice999 မနက်ဖြန် ကျောင်းပိတ်တယ်ဗျာ`)", 
            parse_mode=ParseMode.HTML
        )
        return

    msg = parts[1]
    user_id = update.effective_user.id
    
    allowed_groups = await get_allowed_groups(user_id, context)
    if not allowed_groups:
        await update.message.reply_text("❌ သင်ပို့ခွင့်ရှိသော Group မရှိပါ။ (Group ထဲတွင် မရှိတော့၍ ဖြစ်နိုင်ပါသည်)")
        return

    try:
        identity = await get_user_identity(user_id)
        final_msg = f"<b>#{identity}</b>\n<pre>{html.escape(msg)}</pre>\n\n<i>- {identity}</i>"
        
        for gid in allowed_groups:
            try: 
                await context.bot.send_message(chat_id=gid, text=final_msg, parse_mode=ParseMode.HTML)
            except Exception as e: 
                logger.error(f"Ghost notice failed for group {gid}: {e}")

        if LOG_GROUP_ID != 0:
            display_user = get_safe_user_link(update.effective_user)
            log_txt = (
                f"🚨 <b>SYSTEM LOG | #Notice999 (GHOST MODE)</b>\n"
                f"🎯 <b>Target:</b> ALL ALLOWED GROUPS\n"
                f"👤 <b>User:</b> {display_user} (ID: <code>{user_id}</code>)\n"
                f"🏷️ <b>Identity:</b> {identity}\n"
                f"🕒 <b>Time:</b> {get_mm_time()}\n"
                f"📝 <b>Content:</b>\n<pre>{html.escape(msg)}</pre>"
            )
            try: 
                await context.bot.send_message(LOG_GROUP_ID, text=log_txt, parse_mode=ParseMode.HTML)
            except Exception as e: 
                logger.error(f"Ghost notice log failed: {e}")

        await update_user_cooldown(user_id)
        await update.message.reply_text("<pre>⚡ GHOST NOTICE DELIVERED TO ALL GROUPS.</pre>", parse_mode=ParseMode.HTML)
        
    except Exception as e:
        logger.error(f"Critical error in ghost notice: {e}")
        await update.message.reply_text("<pre>❌ Failed to execute ghost notice.</pre>", parse_mode=ParseMode.HTML)