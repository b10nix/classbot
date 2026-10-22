# utils/error_handler.py

import html
import logging
import traceback
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from utils.constants import LOG_GROUP_ID

logger = logging.getLogger(__name__)

async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global exception handler to catch unhandled errors and notify LOG_GROUP_ID."""
    logger.error("Unhandled exception occurred:", exc_info=context.error)

    # Notify user gracefully if error happened in an active update
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ <b>System Warning:</b> လုပ်ဆောင်ချက်အတွင်း ချို့ယွင်းချက်တစ်ခု ဖြစ်ပေါ်ခဲ့ပါသည်။",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass

    # Send detailed traceback to LOG_GROUP_ID
    if LOG_GROUP_ID != 0:
        tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
        tb_string = "".join(tb_list)
        short_tb = tb_string[-1500:] if len(tb_string) > 1500 else tb_string

        user_info = "System/Job"
        if isinstance(update, Update) and update.effective_user:
            u = update.effective_user
            user_info = f"{html.escape(u.first_name or '')} (<code>{u.id}</code>)"

        err_msg = (
            f"🚨 <b>UNHANDLED EXCEPTION ALERT</b>\n"
            f"👤 <b>Triggered By:</b> {user_info}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"<pre>{html.escape(short_tb)}</pre>"
        )
        try:
            await context.bot.send_message(chat_id=LOG_GROUP_ID, text=err_msg, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.error(f"Failed to send exception log to LOG_GROUP_ID: {e}")