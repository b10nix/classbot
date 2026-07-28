import os
from dotenv import load_dotenv
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler, 
    ConversationHandler, MessageHandler, filters, InlineQueryHandler
)
import datetime
from datetime import timezone, timedelta

# 📌 အဟောင်းတွေကို ကျော်ပြီး .env အသစ်ကိုသာ အတင်းဖတ်ခိုင်းခြင်း (Override)
load_dotenv(override=True)

# 📌 1. Custom Logger ကို utils မှ ခေါ်ယူခြင်း
from utils.logger import get_logger
logger = get_logger(__name__)

# 📌 .env ထဲကနေ Super Admins တွေကို တကယ်ဖတ်မိလားဆိုတာ Log ထုတ်စစ်ဆေးခြင်း
logger.info(f"🟢 Loaded Super Admins: {os.getenv('SUPER_ADMINS')}")

from database.db import connect_db

# ================= 📦 HANDLER IMPORTS =================
from handlers.routing import route_cmd, route_action
from handlers.menu import start, menu_handler
from handlers.admin_tools import import_members, mention_all, get_custom_emoji
from handlers.funds import add_fund, clear_fund, check_fund
from handlers.custom_cmds import add_custom_cmd, del_custom_cmd, handle_custom_cmd
from handlers.weather import weather_cmd, morning_weather_alert
from handlers.student_data import inline_search, add_dummy_data

# 📌 Fund & Reports Imports
from handlers.fund_admin import newfund_cmd, payfund_cmd, setphone_cmd, delfund_cmd, settg_cmd, undo_cmd, history_cmd 
from handlers.reports import list_cmd, report_cmd, vault_cmd, hostel_cmd, export_cmd, remind_cmd, export_fund_cmd

# 📌 User Registration Imports
from handlers.user import user_add_start, user_add_hostel_callback, user_set_phone, ADD_HOSTEL

# 📌 Academic & Timetable Imports
from handlers.academic import (
    add_task_start, t_subject_callback, t_date_receive, t_content_receive, show_tasks,
    T_SUBJECT, T_DATE, T_CONTENT, tasks_reminder_job, class_reminder_job,
    add_class, del_class, clear_day, get_timetable_cmd, change_timetable_day
)

from handlers.tools import (
    show_creator, show_status, show_wifi, paginate_wifi,
    add_wifi, del_wifi, add_egg, del_egg,
    encrypt_decrypt, bin_to_hex, hex_to_bin
)

from handlers.attendance import (
    start_attendance_cmd, close_attendance_action,
    rollcall_start, rollcall_receive_pin, rollcall_cancel, ROLL_PIN_INPUT,
    view_dates_cmd, dates_callback_handler
)

from handlers.skip import (
    skip_start, skip_process_major, skip_process_subject, skip_receive_missed, skip_cancel_cmd,
    SKIP_MAJOR, SKIP_SUBJECT, SKIP_MISSED
)

# 📌 Utilities Imports (Enum States, Helpers)
from utils.states import State
from utils.helpers import cancel_conv

# 📌 Admin Settings Imports
from handlers.admin_settings import (
    set_cooldown_cmd, set_cooldown_action, set_user_cooldown_cmd, set_daily_limit_cmd,
    approvers_cmd, approvers_callback, add_approver_receive
)

# 📌 Social Features (via __init__.py)
from handlers.social import (
    confess_start, confess_receive, confess_action,
    c1_start, c1_receive_msg, c1_receive_name, c1_action,
    post_start, post_receive, post_action, post999_cmd, 
    admin_approval_action,
    nickname_start, nickname_receive, nickname_action,
    reply_start, reply_receive,
)

# handlers.social အုပ်စုထဲမှာ reply_start, reply_receive ကိုပါ ထည့်ခေါ်ပါ
from handlers.social import reply_start, reply_receive

TOKEN = os.getenv("TELEGRAM_TOKEN")

async def on_startup(application):
    logger.info("Starting up Bot and connecting to Database...")
    await connect_db()

def main():
    if not TOKEN:
        logger.error("❌ TELEGRAM_TOKEN is missing!")
        return

    app = ApplicationBuilder().token(TOKEN).post_init(on_startup).build()
    # 📌 Custom Emoji ပါလာရင် ID ထုတ်ပေးမည့် Handler
    app.add_handler(MessageHandler(filters.Entity("custom_emoji"), get_custom_emoji))
    
    # 🚀 Background Jobs
    app.job_queue.run_repeating(class_reminder_job, interval=60, first=10)
    app.job_queue.run_repeating(tasks_reminder_job, interval=3600, first=10)

    # ================= CONVERSATION HANDLERS =================
    
    # 📌 Tasks & Tutorials Add/Edit System 
    t_conv = ConversationHandler(
        entry_points=[
            CommandHandler('addtask', add_task_start),
            CommandHandler('addtutorial', add_task_start),
            CommandHandler('edittask', add_task_start),
            CommandHandler('edittutorial', add_task_start)
        ],
        states={
            T_SUBJECT: [CallbackQueryHandler(t_subject_callback, pattern="^tsubj_")],
            T_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, t_date_receive)],
            T_CONTENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, t_content_receive)]
        },
        fallbacks=[]
    )
    app.add_handler(t_conv)

    # 📌 Custom Confession
    c1_conv = ConversationHandler(
        entry_points=[CommandHandler('confess', c1_start)],
        states={
            State.C1_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, c1_receive_msg)],
            State.C1_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, c1_receive_name)],
            State.C1_CONFIRM: [CallbackQueryHandler(c1_action, pattern='^(send_c1|cancel_c1)')]
        },
        fallbacks=[
            CommandHandler('cancel', cancel_conv),
            CallbackQueryHandler(cancel_conv, pattern='^cancel_') # 📌 ဒီလိုင်းလေး တိုးသွားတာပါ
        ]
    )
    app.add_handler(c1_conv)

    # 📌 Anonymous Confession
    confess_conv = ConversationHandler(
        entry_points=[CommandHandler('confess1', confess_start)],
        states={
            State.CONFESS_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, confess_receive)],
            State.CONFESS_CONFIRM: [CallbackQueryHandler(confess_action, pattern='^(send_confess|cancel_confess)')]
        },
        fallbacks=[
            CommandHandler('cancel', cancel_conv),
            CallbackQueryHandler(cancel_conv, pattern='^cancel_') # 📌 ဒီလိုင်းလေး တိုးသွားတာပါ
        ]
    )
    app.add_handler(confess_conv)

    # 📌 Post System
    post_conv = ConversationHandler(
        entry_points=[CommandHandler('post', post_start)],
        states={
            State.POST_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, post_receive)],
            State.POST_CONFIRM: [CallbackQueryHandler(post_action, pattern='^(send_post|cancel_post)')]
        },
        fallbacks=[
            CommandHandler('cancel', cancel_conv),
            CallbackQueryHandler(cancel_conv, pattern='^cancel_') # 📌 ဒီလိုင်းလေး တိုးသွားတာပါ
        ]
    )
    app.add_handler(post_conv)

    reply_conv = ConversationHandler(
        entry_points=[CommandHandler('start', reply_start, filters.Regex(r'reply_'))], 
        states={
            1: [MessageHandler(filters.TEXT & ~filters.COMMAND, reply_receive)],
        },
        fallbacks=[
            CommandHandler('cancel', cancel_conv),
            CallbackQueryHandler(cancel_conv, pattern='^cancel_')
        ]
    )
    app.add_handler(reply_conv)
    
    # 📌 Admin Approvers Management
    approver_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(approvers_callback, pattern="^(addapp_|delapp_|cancel_app)")],
        states={
            State.APPROVER_ADD: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_approver_receive)]
        },
        fallbacks=[CommandHandler("cancel", cancel_conv)]
    )
    app.add_handler(approver_conv)
    
    # 📌 Nickname Setup
    nickname_conv = ConversationHandler(
        entry_points=[CommandHandler('nickname', nickname_start)],
        states={
            1: [MessageHandler(filters.TEXT & ~filters.COMMAND, nickname_receive)],
            2: [CallbackQueryHandler(nickname_action, pattern='^savenick_')] # 📌 ဤနေရာမှ savenick ခလုတ်များကို ဖမ်းယူသည်
        },
        fallbacks=[
            CommandHandler('cancel', cancel_conv),
            CallbackQueryHandler(cancel_conv, pattern='^cancel_')
        ]
    )
    app.add_handler(nickname_conv)

    # 📌 Self Registration System
    user_add_conv = ConversationHandler(
        entry_points=[CommandHandler('add', user_add_start)],
        states={
            ADD_HOSTEL: [CallbackQueryHandler(user_add_hostel_callback, pattern="^hostel_")]
        },
        fallbacks=[]
    )
    app.add_handler(user_add_conv)

    # 📌 Roll Call System
    roll_conv = ConversationHandler(
        entry_points=[CommandHandler('rollcall', rollcall_start)],
        states={
            ROLL_PIN_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, rollcall_receive_pin)]
        },
        fallbacks=[CommandHandler('cancel', rollcall_cancel)]
    )
    app.add_handler(roll_conv)

    # 📌 Skip Calculator
    skip_conv = ConversationHandler(
        entry_points=[CommandHandler('skip', skip_start)],
        states={
            SKIP_MAJOR: [CallbackQueryHandler(skip_process_major, pattern="^skipmaj_|^skip_cancel$")],
            SKIP_SUBJECT: [CallbackQueryHandler(skip_process_subject, pattern="^skipsub_|^skip_cancel$")],
            SKIP_MISSED: [MessageHandler(filters.TEXT & ~filters.COMMAND, skip_receive_missed)]
        },
        fallbacks=[CommandHandler('cancel', skip_cancel_cmd)]
    )
    app.add_handler(skip_conv)

    # ================= STANDARD COMMANDS & HANDLERS =================
    
    # Main Menu & Start Command
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CallbackQueryHandler(menu_handler, pattern="^(menu_|back_to_main)"))

    # Legacy Fund Commands
    app.add_handler(CommandHandler("addfund", add_fund))
    app.add_handler(CommandHandler("clearfund", clear_fund))
    app.add_handler(CommandHandler("fund", check_fund))

    # Cooldown & Limits Management
    app.add_handler(CommandHandler("setcooldown", set_cooldown_cmd))
    app.add_handler(CallbackQueryHandler(set_cooldown_action, pattern="^setcd_"))
    app.add_handler(CommandHandler("setusercd", set_user_cooldown_cmd))
    app.add_handler(CommandHandler("setlimit", set_daily_limit_cmd))
    app.add_handler(CallbackQueryHandler(admin_approval_action, pattern="^(aprv_|rjct_)"))

    # General Tools & Information
    app.add_handler(CommandHandler('creator', show_creator))
    app.add_handler(CommandHandler('status', show_status))
    app.add_handler(CommandHandler('wifi', show_wifi))
    app.add_handler(CallbackQueryHandler(paginate_wifi, pattern="^wifi_page_"))
    
    # Admin System Commands
    app.add_handler(CommandHandler('addwifi', add_wifi))
    app.add_handler(CommandHandler('delwifi', del_wifi))
    app.add_handler(CommandHandler('addegg', add_egg))
    app.add_handler(CommandHandler('delegg', del_egg))
    app.add_handler(CommandHandler("post999", post999_cmd))

    # Attendance & Roll Call Dates
    app.add_handler(CommandHandler('attendance', start_attendance_cmd))
    app.add_handler(CommandHandler('dates', view_dates_cmd))
    app.add_handler(CallbackQueryHandler(close_attendance_action, pattern="^att_close_"))
    app.add_handler(CallbackQueryHandler(dates_callback_handler, pattern="^vdate_"))

    # Routing Management
    app.add_handler(CommandHandler('routes', route_cmd))
    app.add_handler(CallbackQueryHandler(route_action, pattern="^route_"))

    # Crypto & Data Tools
    app.add_handler(CommandHandler(['encrypt', 'decrypt'], encrypt_decrypt))
    app.add_handler(CommandHandler('bin2hex', bin_to_hex))
    app.add_handler(CommandHandler('hex2bin', hex_to_bin))

    # Custom Commands Management
    app.add_handler(CommandHandler(['addcmd1', 'addcmd2', 'addcmd3'], add_custom_cmd))
    app.add_handler(CommandHandler('delcmd', del_custom_cmd))

    # Catch-all for Custom Commands
    app.add_handler(MessageHandler(filters.COMMAND, handle_custom_cmd), group=2)

    app.add_handler(CommandHandler("approvers", approvers_cmd))
    
    app.add_handler(CommandHandler("import", import_members))
    app.add_handler(CommandHandler("mention", mention_all))
    app.add_handler(CommandHandler("addclass", add_class))
    app.add_handler(CommandHandler("delclass", del_class))
    app.add_handler(CommandHandler("clearday", clear_day))
    
    # 📌 Weather Command
    app.add_handler(CommandHandler('weather', weather_cmd))

    # 📌 Morning Auto Weather Alert
    mm_tz = timezone(timedelta(hours=6, minutes=30))
    alert_time = datetime.time(hour=7, minute=30, tzinfo=mm_tz)
    app.job_queue.run_daily(morning_weather_alert, time=alert_time, days=(0, 1, 2, 3, 4))

    # Task Management
    app.add_handler(CommandHandler('tasks', show_tasks))
    app.add_handler(CommandHandler('tutorials', show_tasks))

    # 📌 Dummy Data & Inline Search
    app.add_handler(CommandHandler('adddummy', add_dummy_data))
    app.add_handler(InlineQueryHandler(inline_search))

    # 📌 Funds System
    app.add_handler(CommandHandler('newfund', newfund_cmd))
    app.add_handler(CommandHandler('pay', payfund_cmd))
    app.add_handler(CommandHandler('delfund', delfund_cmd))
    app.add_handler(CommandHandler('setphone', setphone_cmd))
    app.add_handler(CommandHandler('undo', undo_cmd))
    app.add_handler(CommandHandler("history", history_cmd))

    app.add_handler(CommandHandler('ph', user_set_phone))

    # 📌 Reports & Dashboards
    app.add_handler(CommandHandler('list', list_cmd))
    app.add_handler(CommandHandler('report', report_cmd))
    app.add_handler(CommandHandler('vault', vault_cmd))
    app.add_handler(CommandHandler('hostel', hostel_cmd))
    app.add_handler(CommandHandler('export', export_cmd))
    app.add_handler(CommandHandler('exportfund', export_fund_cmd))
    app.add_handler(CommandHandler('remind', remind_cmd))

    # 📌 Timetables
    MAJORS = ["it", "mc", "archi", "ep", "ec", "me", "civil"]
    for major in MAJORS:
        app.add_handler(CommandHandler(major, get_timetable_cmd))

    app.add_handler(CallbackQueryHandler(change_timetable_day, pattern="^tt_"))

    logger.info("🟢 CLASS BOT SYSTEM ONLINE...")
    app.run_polling()

if __name__ == '__main__':
    main()