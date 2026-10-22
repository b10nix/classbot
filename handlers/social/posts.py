import uuid
import logging
import asyncio
import html
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode
from database.db import get_db

from utils.constants import OWNER_ID, LOG_GROUP_ID, FOOTER
from utils.states import State
from utils.helpers import (
    get_mm_time,
    schedule_delete,
    build_target_keyboard,
    get_user_identity,
    get_safe_user_link,
    get_allowed_groups,
    get_group_name
)
from utils.security import (
    check_daily_limit,
    increment_daily_limit,
    check_user_cooldown,
    update_user_cooldown,
    show_cooldown_warning
)

logger = logging.getLogger(__name__)

async def enforce_private_chat_and_membership(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Validates class group membership and ensures interaction only occurs in private chat (DM).
    If invoked in a public group, warns the user and schedules deletion of both the command
    and the bot response within 10 seconds.
    """
    user_id = update.effective_user.id
    chat = update.effective_chat

    # 1. Class Group Member Authorization Check
    allowed_groups = await get_allowed_groups(user_id, context)
    if not allowed_groups:
        denied_msg = await update.message.reply_text(
            "⛔ <b>Access Denied:</b> ဤ Feature ကို Class Group Member များသာ အသုံးပြုခွင့်ရှိပါသည်။",
            parse_mode=ParseMode.HTML
        )
        if chat.type != 'private':
            schedule_delete(context, chat.id, update.message.message_id, 10)
            schedule_delete(context, chat.id, denied_msg.message_id, 10)
        return False

    # 2. Private Chat (DM) Enforcement
    if chat.type != 'private':
        bot_username = context.bot.username
        dm_link = f"https://t.me/{bot_username}"
        warn_text = (
            f"⚠️ ဤ Command ကို Bot ၏ DM (Private Chat) တွင်သာ အသုံးပြုနိုင်ပါသည်။\n"
            f"👉 <a href='{dm_link}'>ဒီနေရာကိုနှိပ်၍ DM တွင် စတင်ပါ</a>"
        )
        warn_msg = await update.message.reply_text(
            warn_text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )
        # Delete both user command and warning notification within 10 seconds
        schedule_delete(context, chat.id, update.message.message_id, 10)
        schedule_delete(context, chat.id, warn_msg.message_id, 10)
        return False

    return True


# ================= ANONYMOUS CONFESSION (/confess1) =================
async def confess_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await enforce_private_chat_and_membership(update, context):
        return ConversationHandler.END

    user_id = update.effective_user.id
    daily_ok, limit_count = await check_daily_limit(user_id)
    if not daily_ok:
        warn = await update.message.reply_text(
            f"🚫 <b>DAILY LIMIT REACHED</b>\n\nယနေ့အတွက် သတ်မှတ်ထားသော ကန့်သတ်ချက် (<b>{limit_count}</b> ခု) ပြည့်သွားပါပြီ။",
            parse_mode=ParseMode.HTML
        )
        schedule_delete(context, update.effective_chat.id, warn.message_id, 15)
        return ConversationHandler.END

    remain_sec = await check_user_cooldown(user_id)
    if remain_sec > 0:
        await show_cooldown_warning(update, remain_sec, context)
        return ConversationHandler.END

    kb = [[InlineKeyboardButton("❌ Cancel", callback_data="cancel_confess")]]
    prompt = await update.message.reply_text(
        "<b>🕵️ ANONYMOUS CONFESSION</b>\n<pre>Enter your message (Identity Hidden):</pre>",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML
    )
    context.user_data['prompt_msg_id'] = prompt.message_id
    return State.CONFESS_MSG

async def confess_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['msg'] = update.message.text

    try:
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=context.user_data.get('prompt_msg_id'))
    except Exception:
        pass

    markup = await build_target_keyboard(update.effective_user.id, context, "send_confess", "cancel_confess")
    await update.message.reply_text(
        f"<b>📝 VERIFY & SELECT TARGET</b>\n<pre>{html.escape(update.message.text)}</pre>",
        reply_markup=markup, parse_mode=ParseMode.HTML
    )
    return State.CONFESS_CONFIRM

async def confess_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        if query.data.startswith("send_confess"):
            chat_id = query.message.chat_id
            msg_id = query.message.message_id
            user = update.effective_user
            user_id = user.id
            first_name = user.first_name or "User"
            username = user.username

            await context.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text="<pre>🤖 AI ဖြင့် စစ်ဆေးနေပါသည်... [■■■■■]</pre>", parse_mode=ParseMode.HTML)
            await asyncio.sleep(0.5)

            target = query.data.replace("send_confess_", "")
            target_name = "All Class Groups" if target == "all" else await get_group_name(context.bot, int(target))

            msg = context.user_data.get('msg', '')
            post_id = str(uuid.uuid4())[:8]
            log_id = f"LOG #{post_id[:5].upper()}"
            db = get_db()
            admin_msgs = []
            log_msg_id = None

            if db is not None:
                display_user = get_safe_user_link(user)

                if LOG_GROUP_ID != 0:
                    log_txt = (
                        f"🟡 <b>{log_id} • CONFESSION (ANON)</b>\n\n"
                        f"👤 {display_user}\n"
                        f"🆔 <code>{user_id}</code>\n\n"
                        f"🎭 Anonymous\n"
                        f"🎯 {target_name}\n"
                        f"🕒 {get_mm_time()}\n\n"
                        f"━━━━━━━━━━━━━━━━━━\n\n"
                        f"{html.escape(msg)}\n\n"
                        f"━━━━━━━━━━━━━━━━━━\n\n"
                        f"⏳ <i>Pending Review...</i>"
                    )
                    try:
                        log_m = await context.bot.send_message(LOG_GROUP_ID, text=log_txt, parse_mode=ParseMode.HTML)
                        log_msg_id = log_m.message_id
                    except Exception as e:
                        logger.error(f"Failed to send log to LOG_GROUP_ID: {e}")

                approvers = await db.approvers.find({}).to_list(length=None)
                approver_ids = [a['user_id'] for a in approvers]
                if OWNER_ID not in approver_ids:
                    approver_ids.append(OWNER_ID)

                identity = await get_user_identity(user_id)
                dm_txt = (
                    f"🚨 <b>PENDING CONFESSION</b>\n"
                    f"👤 <b>Sender:</b> {identity}\n"
                    f"🎯 <b>Target:</b> {target_name}\n\n"
                    f"📝 <b>Message:</b>\n"
                    f"<pre>{html.escape(msg)}</pre>"
                )
                kb = [[
                    InlineKeyboardButton("✅ APPROVE", callback_data=f"aprv_{post_id}"),
                    InlineKeyboardButton("❌ REJECT", callback_data=f"rjct_{post_id}")
                ]]

                for adm_id in approver_ids:
                    try:
                        m = await context.bot.send_message(adm_id, text=dm_txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
                        admin_msgs.append({"chat_id": adm_id, "msg_id": m.message_id})
                    except Exception:
                        pass

                await db.pending_posts.insert_one({
                    "post_id": post_id,
                    "type": "confess_anon",
                    "msg": msg,
                    "target": target,
                    "sender_id": user_id,
                    "sender_first_name": first_name,
                    "sender_username": username,
                    "time": get_mm_time(),
                    "admin_msgs": admin_msgs,
                    "log_msg_id": log_msg_id
                })

            await update_user_cooldown(user_id)
            await increment_daily_limit(user_id)

            await context.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text="<pre>✅ AI အကဲဖြတ်မှု အောင်မြင်ပါသည်။ Admin ထံသို့ ပို့ပြီးပါပြီ။</pre>", parse_mode=ParseMode.HTML)
            schedule_delete(context, chat_id, msg_id, 7)
        else:
            await query.edit_message_text("<pre>🚫 ABORTED.</pre>", parse_mode=ParseMode.HTML)
            schedule_delete(context, query.message.chat_id, query.message.message_id, 3)

    except Exception as e:
        logger.error(f"Error in confess_action: {e}")
        await query.edit_message_text("<pre>❌ Error: System Issue</pre>", parse_mode=ParseMode.HTML)
        schedule_delete(context, query.message.chat_id, query.message.message_id, 5)

    return ConversationHandler.END


# ================= CUSTOM CONFESSION (/confess) =================
async def c1_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await enforce_private_chat_and_membership(update, context):
        return ConversationHandler.END

    user_id = update.effective_user.id
    daily_ok, limit_count = await check_daily_limit(user_id)
    if not daily_ok:
        warn = await update.message.reply_text(
            f"🚫 <b>DAILY LIMIT REACHED</b>\n\nယနေ့အတွက် သတ်မှတ်ထားသော ကန့်သတ်ချက် (<b>{limit_count}</b> ခု) ပြည့်သွားပါပြီ။",
            parse_mode=ParseMode.HTML
        )
        schedule_delete(context, update.effective_chat.id, warn.message_id, 15)
        return ConversationHandler.END

    remain_sec = await check_user_cooldown(user_id)
    if remain_sec > 0:
        await show_cooldown_warning(update, remain_sec, context)
        return ConversationHandler.END

    kb = [[InlineKeyboardButton("❌ Cancel", callback_data="cancel_c1")]]
    prompt = await update.message.reply_text(
        "<b>🎭 CONFESSION SYSTEM (NAME REQUIRED)</b>\n<pre>Enter your confession message:</pre>",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML
    )
    context.user_data['prompt_msg_id'] = prompt.message_id
    return State.C1_MSG

async def c1_receive_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['c1_msg'] = update.message.text

    try:
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=context.user_data.get('prompt_msg_id'))
    except Exception:
        pass

    kb = [[InlineKeyboardButton("❌ Cancel", callback_data="cancel_c1")]]
    prompt = await update.message.reply_text(
        "<b>✍️ SIGNATURE</b>\n<pre>Enter the name/signature you want to show (e.g. Ben):</pre>",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML
    )
    context.user_data['prompt_msg_id'] = prompt.message_id
    return State.C1_NAME

async def c1_receive_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['c1_name'] = update.message.text

    try:
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=context.user_data.get('prompt_msg_id'))
    except Exception:
        pass

    markup = await build_target_keyboard(update.effective_user.id, context, "send_c1", "cancel_c1")
    await update.message.reply_text(
        f"<b>📝 PREVIEW & SELECT TARGET</b>\n<pre>{html.escape(context.user_data['c1_msg'])}</pre>\n\n<i>- {html.escape(context.user_data['c1_name'])}</i>",
        reply_markup=markup, parse_mode=ParseMode.HTML
    )
    return State.C1_CONFIRM

async def c1_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        if query.data.startswith("send_c1"):
            chat_id = query.message.chat_id
            msg_id = query.message.message_id
            user = update.effective_user
            user_id = user.id
            first_name = user.first_name or "User"
            username = user.username

            await context.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text="<pre>🤖 AI ဖြင့် စစ်ဆေးနေပါသည်... [■■■■■]</pre>", parse_mode=ParseMode.HTML)
            await asyncio.sleep(0.5)

            target = query.data.replace("send_c1_", "")
            target_name = "All Class Groups" if target == "all" else await get_group_name(context.bot, int(target))

            msg = context.user_data.get('c1_msg', '')
            name = context.user_data.get('c1_name', 'Anonymous')
            post_id = str(uuid.uuid4())[:8]
            log_id = f"LOG #{post_id[:5].upper()}"
            db = get_db()
            admin_msgs = []
            log_msg_id = None

            if db is not None:
                display_user = get_safe_user_link(user)

                if LOG_GROUP_ID != 0:
                    log_txt = (
                        f"🟡 <b>{log_id} • CONFESSION (CUSTOM)</b>\n\n"
                        f"👤 {display_user}\n"
                        f"🆔 <code>{user_id}</code>\n\n"
                        f"🎭 {html.escape(name)}\n"
                        f"🎯 {target_name}\n"
                        f"🕒 {get_mm_time()}\n\n"
                        f"━━━━━━━━━━━━━━━━━━\n\n"
                        f"{html.escape(msg)}\n\n"
                        f"━━━━━━━━━━━━━━━━━━\n\n"
                        f"⏳ <i>Pending Review...</i>"
                    )
                    try:
                        log_m = await context.bot.send_message(LOG_GROUP_ID, text=log_txt, parse_mode=ParseMode.HTML)
                        log_msg_id = log_m.message_id
                    except Exception as e:
                        logger.error(f"Failed to send log to LOG_GROUP_ID: {e}")

                approvers = await db.approvers.find({}).to_list(length=None)
                approver_ids = [a['user_id'] for a in approvers]
                if OWNER_ID not in approver_ids:
                    approver_ids.append(OWNER_ID)

                identity = await get_user_identity(user_id)
                dm_txt = (
                    f"🚨 <b>PENDING CONFESSION (CUSTOM)</b>\n"
                    f"👤 <b>Sender:</b> {identity}\n"
                    f"🎯 <b>Target:</b> {target_name}\n"
                    f"🎭 <b>Signature:</b> {html.escape(name)}\n\n"
                    f"📝 <b>Message:</b>\n"
                    f"<pre>{html.escape(msg)}</pre>"
                )
                kb = [[
                    InlineKeyboardButton("✅ APPROVE", callback_data=f"aprv_{post_id}"),
                    InlineKeyboardButton("❌ REJECT", callback_data=f"rjct_{post_id}")
                ]]

                for adm_id in approver_ids:
                    try:
                        m = await context.bot.send_message(adm_id, text=dm_txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
                        admin_msgs.append({"chat_id": adm_id, "msg_id": m.message_id})
                    except Exception:
                        pass

                await db.pending_posts.insert_one({
                    "post_id": post_id,
                    "type": "confess_custom",
                    "msg": msg,
                    "target": target,
                    "name": name,
                    "sender_id": user_id,
                    "sender_first_name": first_name,
                    "sender_username": user.username,
                    "time": get_mm_time(),
                    "admin_msgs": admin_msgs,
                    "log_msg_id": log_msg_id
                })

            await update_user_cooldown(user_id)
            await increment_daily_limit(user_id)

            await context.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text="<pre>✅ AI အကဲဖြတ်မှု အောင်မြင်ပါသည်။ Admin ထံသို့ ပို့ပြီးပါပြီ။</pre>", parse_mode=ParseMode.HTML)
            schedule_delete(context, chat_id, msg_id, 7)
        else:
            await query.edit_message_text("<pre>🚫 ABORTED.</pre>", parse_mode=ParseMode.HTML)
            schedule_delete(context, query.message.chat_id, query.message.message_id, 3)

    except Exception as e:
        logger.error(f"Error in c1_action: {e}")
        await query.edit_message_text("<pre>❌ Error: System Issue</pre>", parse_mode=ParseMode.HTML)
        schedule_delete(context, query.message.chat_id, query.message.message_id, 5)

    return ConversationHandler.END


# ================= POST SYSTEM (/post) =================
async def post_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await enforce_private_chat_and_membership(update, context):
        return ConversationHandler.END

    user_id = update.effective_user.id
    daily_ok, limit_count = await check_daily_limit(user_id)
    if not daily_ok:
        warn = await update.message.reply_text(
            f"🚫 <b>DAILY LIMIT REACHED</b>\n\nယနေ့အတွက် သတ်မှတ်ထားသော ကန့်သတ်ချက် (<b>{limit_count}</b> ခု) ပြည့်သွားပါပြီ။",
            parse_mode=ParseMode.HTML
        )
        schedule_delete(context, update.effective_chat.id, warn.message_id, 15)
        return ConversationHandler.END

    remain_sec = await check_user_cooldown(user_id)
    if remain_sec > 0:
        await show_cooldown_warning(update, remain_sec, context)
        return ConversationHandler.END

    kb = [[InlineKeyboardButton("❌ Cancel", callback_data="cancel_post")]]
    prompt = await update.message.reply_text(
        "<b>💬 SECURE POST SYSTEM</b>\n<pre>Enter your post message:</pre>",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML
    )
    context.user_data['prompt_msg_id'] = prompt.message_id
    return State.POST_MSG

async def post_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['post_msg'] = update.message.text

    try:
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=context.user_data.get('prompt_msg_id'))
    except Exception:
        pass

    markup = await build_target_keyboard(update.effective_user.id, context, "send_post", "cancel_post")
    await update.message.reply_text(
        f"<b>📝 VERIFY & SELECT TARGET</b>\n<pre>{html.escape(update.message.text)}</pre>",
        reply_markup=markup, parse_mode=ParseMode.HTML
    )
    return State.POST_CONFIRM

async def post_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        if query.data.startswith("send_post"):
            target = query.data.replace("send_post_", "")
            target_name = "All Class Groups" if target == "all" else await get_group_name(context.bot, int(target))

            allowed_groups = await get_allowed_groups(update.effective_user.id, context)
            target_ids = allowed_groups if target == "all" else [int(target)]

            msg = context.user_data.get('post_msg', '')
            post_id = str(uuid.uuid4())[:8]
            log_id = f"LOG #{post_id[:5].upper()}"

            user = update.effective_user
            user_id = user.id
            first_name = user.first_name or "User"
            username = user.username

            identity = await get_user_identity(user_id)
            final_msg = f"💬 <b>POST</b>\n\n<pre>{html.escape(msg)}</pre>\n\n- {identity}{FOOTER}"

            bot_username = context.bot.username
            db = get_db()
            published_targets = []

            if LOG_GROUP_ID != 0:
                display_user = get_safe_user_link(user)
                log_txt = (
                    f"🟢 <b>{log_id} • POST (DIRECT)</b>\n\n"
                    f"👤 {display_user}\n"
                    f"🆔 <code>{user_id}</code>\n\n"
                    f"🏷️ {identity}\n"
                    f"🎯 {target_name}\n"
                    f"🕒 {get_mm_time()}\n\n"
                    f"━━━━━━━━━━━━━━━━━━\n\n"
                    f"{html.escape(msg)}\n\n"
                    f"━━━━━━━━━━━━━━━━━━\n\n"
                    f"✅ <i>Delivered</i>"
                )
                try:
                    await context.bot.send_message(LOG_GROUP_ID, text=log_txt, parse_mode=ParseMode.HTML)
                except Exception as e:
                    logger.error(f"Failed to send direct post log to LOG_GROUP_ID: {e}")

            for gid in target_ids:
                try:
                    reply_markup = InlineKeyboardMarkup([[
                        InlineKeyboardButton(f"💬 Reply to {identity}", url=f"https://t.me/{bot_username}?start=reply_{post_id}_{gid}")
                    ]])
                    sent_msg = await context.bot.send_message(chat_id=gid, text=final_msg, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
                    published_targets.append({"chat_id": gid, "msg_id": sent_msg.message_id})
                except Exception:
                    pass

            if published_targets and db is not None:
                await db.published_posts.insert_one({
                    "post_id": post_id,
                    "signature": identity,
                    "sender_id": user_id,
                    "sender_first_name": first_name,
                    "sender_username": username,
                    "targets": published_targets
                })

            await update_user_cooldown(user_id)
            await increment_daily_limit(user_id)
            await query.edit_message_text("<pre>✅ POST DELIVERED SUCCESSFULLY.</pre>", parse_mode=ParseMode.HTML)
            schedule_delete(context, query.message.chat_id, query.message.message_id, 7)
        else:
            await query.edit_message_text("<pre>🚫 ABORTED.</pre>", parse_mode=ParseMode.HTML)
            schedule_delete(context, query.message.chat_id, query.message.message_id, 3)

    except Exception as e:
        logger.error(f"Error in post_action: {e}")
        await query.edit_message_text("<pre>❌ Error: System Issue</pre>", parse_mode=ParseMode.HTML)
        schedule_delete(context, query.message.chat_id, query.message.message_id, 5)

    return ConversationHandler.END


# ================= 👻 HIDDEN FEATURE: GHOST POST (/post999) =================
async def post999_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pass