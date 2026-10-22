# handlers/register.py

from datetime import timedelta

from telegram import Update
from telegram.ext import (
    CommandHandler, CallbackQueryHandler, ConversationHandler,
    MessageHandler, filters, InlineQueryHandler, TypeHandler
)

# ================= 📦 HANDLER IMPORTS =================
from handlers.core.routing import route_cmd, route_action
from handlers.core.menu import start, menu_handler
from handlers.admin.admin_tools import import_members, mention_all, get_custom_emoji
from handlers.admin.major_admin import (
    setmajor_cmd, addec_cmd, delec_cmd, eclist_cmd,
    setsemester_cmd, setsemester_callback, setgroupmajor_cmd, reconcilerolls_cmd
)
from handlers.core.custom_cmds import add_custom_cmd, del_custom_cmd, handle_custom_cmd
from handlers.tools.weather import weather_cmd
from handlers.student.student_data import inline_search, add_dummy_data
from handlers.student.roll_change import force_roll_cmd

# 📌 Fund & Reports Imports
from handlers.finance.fund_admin import (
    newfund_cmd, payfund_cmd, setphone_cmd,
    settg_cmd, history_cmd, fund_flow_callback, fund_flow_text_handler,
    funds_cmd, fund_manage_callback
)
from handlers.finance.reports import (
    list_cmd, vault_cmd, hostel_cmd,
    export_cmd, export_callback,
    semhistory_cmd, semhistory_callback
)

# 📌 User Registration Imports
from handlers.student.user import (
    user_register_start,
    user_register_legacy_redirect,
    user_register_receive_roll,
    user_register_major_callback,
    user_register_receive_name,
    user_register_existing_phone_callback,
    user_register_receive_phone,
    user_register_hostel_callback,
    user_register_confirm_callback,
    user_register_cancel_cmd,
    user_register_cancel_callback,
    user_register_timeout,
    user_myprofile_cmd,
    REGISTER_ROLL,
    REGISTER_MAJOR_SELECT,
    REGISTER_NAME,
    REGISTER_PHONE,
    REGISTER_HOSTEL,
    REGISTER_CONFIRM,
    REGISTER_EXISTING_ACTION,
    REGISTER_PHONE_UPDATE_CONFIRM,
)

# 📌 Finance & Fund Management Imports
from handlers.finance.finance_tools import (
    allocate_fund, spend_cmd, assign_ec_cmd, ec_me,
    ecstat_cmd, ecstat_callback, export_finance_report
)
from handlers.finance.nuke_approval import nuke_funds_cmd, nuke_approval_callback
from handlers.finance.fund_selection import (
    undo_cmd, archive_fund_cmd, unarchive_fund_cmd, report_cmd, fundstat_cmd,
    remind_cmd, export_fund_cmd, fund_selection_callback,
)

# 📌 Academic & Timetable Imports
from handlers.academic.academic import (
    add_task_start, t_subject_callback, t_date_receive, t_time_receive,
    t_lead_hours_receive, t_content_receive, t_cancel_callback, show_tasks,
    autott_off_cmd, autott_on_cmd, autott_status_cmd,
    T_SUBJECT, T_DATE, T_TIME, T_LEAD_HOURS, T_CONTENT,
    add_class, del_class, clear_day, get_timetable_cmd, change_timetable_day
)

from handlers.tools.tools import (
    show_creator, show_status, show_wifi, paginate_wifi,
    add_wifi, del_wifi, add_egg, del_egg,
    encrypt_decrypt, bin_to_hex, hex_to_bin
)

from handlers.academic.attendance import (
    start_attendance_cmd, close_attendance_action,
    rollcall_start, rollcall_receive_pin, rollcall_cancel, ROLL_PIN_INPUT,
    view_dates_cmd, dates_callback_handler
)

from handlers.academic.skip import (
    skip_start, skip_process_major, skip_process_subject, skip_receive_missed, skip_cancel_cmd,
    SKIP_MAJOR, SKIP_SUBJECT, SKIP_MISSED
)

# 📌 Utilities Imports (Enum States, Helpers, Constants)
from utils.states import State
from utils.helpers import cancel_conv, sync_user_username_on_update
from utils.constants import MAJORS
from utils.error_handler import global_error_handler

# 📌 Admin Settings Imports
from handlers.admin.admin_settings import (
    set_cooldown_cmd, set_cooldown_action, set_user_cooldown_cmd, set_daily_limit_cmd,
    approvers_cmd, approvers_callback, add_approver_receive
)

# 📌 End Semester
from handlers.admin.endsemester import end_semester_cmd

# 📌 Social Features
from handlers.social import (
    confess_start, confess_receive, confess_action,
    c1_start, c1_receive_msg, c1_receive_name, c1_action,
    post_start, post_receive, post_action, post999_cmd, admin_approval_action,
    nickname_start, nickname_receive, nickname_action, reset_nickname_quota_cmd,
    reply_start, reply_receive
)


def register_all_handlers(app):
    """Register all handlers, conversations, callbacks, and error handler to the application."""

    # 🛡️ Global Error Handler
    app.add_error_handler(global_error_handler)

    # Keep usernames fresh whenever a Telegram user sends a message, presses a
    # callback button, uses inline search, or otherwise interacts with the bot.
    # Group -1 runs before the feature handlers in the default group (0).
    app.add_handler(TypeHandler(Update, sync_user_username_on_update), group=-1)

    # 📌 Custom Emoji
    app.add_handler(MessageHandler(filters.Entity("custom_emoji"), get_custom_emoji))

    # ================= CONVERSATION HANDLERS =================
    t_conv = ConversationHandler(
        entry_points=[
            CommandHandler('addtask', add_task_start),
            CommandHandler('addtutorial', add_task_start),
            CommandHandler('edittask', add_task_start),
            CommandHandler('edittutorial', add_task_start)
        ],
        states={
            T_SUBJECT: [CallbackQueryHandler(t_subject_callback, pattern="^tsubj_"), CallbackQueryHandler(t_cancel_callback, pattern="^task_cancel$")],
            T_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, t_date_receive), CallbackQueryHandler(t_cancel_callback, pattern="^task_cancel$")],
            T_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, t_time_receive), CallbackQueryHandler(t_cancel_callback, pattern="^task_cancel$")],
            T_LEAD_HOURS: [MessageHandler(filters.TEXT & ~filters.COMMAND, t_lead_hours_receive), CallbackQueryHandler(t_cancel_callback, pattern="^task_cancel$")],
            T_CONTENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, t_content_receive), CallbackQueryHandler(t_cancel_callback, pattern="^task_cancel$")]
        },
        fallbacks=[CommandHandler("cancel", t_cancel_callback), CallbackQueryHandler(t_cancel_callback, pattern="^task_cancel$")]
    )
    app.add_handler(t_conv)

    c1_conv = ConversationHandler(
        entry_points=[CommandHandler('confess', c1_start)],
        states={
            State.C1_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, c1_receive_msg)],
            State.C1_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, c1_receive_name)],
            State.C1_CONFIRM: [CallbackQueryHandler(c1_action, pattern='^(send_c1|cancel_c1)')]
        },
        fallbacks=[
            CommandHandler('cancel', cancel_conv),
            CallbackQueryHandler(cancel_conv, pattern='^cancel_')
        ]
    )
    app.add_handler(c1_conv)

    confess_conv = ConversationHandler(
        entry_points=[CommandHandler('confess1', confess_start)],
        states={
            State.CONFESS_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, confess_receive)],
            State.CONFESS_CONFIRM: [CallbackQueryHandler(confess_action, pattern='^(send_confess|cancel_confess)')]
        },
        fallbacks=[
            CommandHandler('cancel', cancel_conv),
            CallbackQueryHandler(cancel_conv, pattern='^cancel_')
        ]
    )
    app.add_handler(confess_conv)

    post_conv = ConversationHandler(
        entry_points=[CommandHandler('post', post_start)],
        states={
            State.POST_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, post_receive)],
            State.POST_CONFIRM: [CallbackQueryHandler(post_action, pattern='^(send_post|cancel_post)')]
        },
        fallbacks=[
            CommandHandler('cancel', cancel_conv),
            CallbackQueryHandler(cancel_conv, pattern='^cancel_')
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

    approver_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(approvers_callback, pattern="^(addapp_|delapp_|cancel_app)")],
        states={
            State.APPROVER_ADD: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_approver_receive)]
        },
        fallbacks=[CommandHandler("cancel", cancel_conv)]
    )
    app.add_handler(approver_conv)

    nickname_conv = ConversationHandler(
        entry_points=[CommandHandler('nickname', nickname_start)],
        states={
            1: [MessageHandler(filters.TEXT & ~filters.COMMAND, nickname_receive)],
            2: [CallbackQueryHandler(nickname_action, pattern='^savenick_')]
        },
        fallbacks=[
            CommandHandler('cancel', cancel_conv),
            CallbackQueryHandler(cancel_conv, pattern='^cancel_')
        ]
    )
    app.add_handler(nickname_conv)

    user_register_conv = ConversationHandler(
        entry_points=[CommandHandler("register", user_register_start)],
        states={
            REGISTER_ROLL: [MessageHandler(filters.TEXT & ~filters.COMMAND, user_register_receive_roll)],
            REGISTER_MAJOR_SELECT: [CallbackQueryHandler(user_register_major_callback, pattern=r"^register_major:")],
            REGISTER_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, user_register_receive_name)],
            REGISTER_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, user_register_receive_phone)],
            REGISTER_HOSTEL: [CallbackQueryHandler(user_register_hostel_callback, pattern=r"^register_hostel:")],
            REGISTER_CONFIRM: [CallbackQueryHandler(user_register_confirm_callback, pattern=r"^register_confirm$")],
            REGISTER_EXISTING_ACTION: [CallbackQueryHandler(user_register_existing_phone_callback, pattern=r"^register_existing_phone$")],
            REGISTER_PHONE_UPDATE_CONFIRM: [CallbackQueryHandler(user_register_confirm_callback, pattern=r"^register_update_phone_confirm$")],
            ConversationHandler.TIMEOUT: [
                MessageHandler(filters.ALL, user_register_timeout),
                CallbackQueryHandler(user_register_timeout),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", user_register_cancel_cmd),
            CallbackQueryHandler(user_register_cancel_callback, pattern=r"^register_cancel$"),
        ],
        allow_reentry=True,
        conversation_timeout=timedelta(minutes=15),
        name="student_registration",
        persistent=False,
    )
    app.add_handler(user_register_conv)

    roll_conv = ConversationHandler(
        entry_points=[CommandHandler('rollcall', rollcall_start)],
        states={
            ROLL_PIN_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, rollcall_receive_pin)]
        },
        fallbacks=[CommandHandler('cancel', rollcall_cancel)]
    )
    app.add_handler(roll_conv)

    skip_conv = ConversationHandler(
        entry_points=[CommandHandler('skip', skip_start)],
        states={
            SKIP_MAJOR: [
                CallbackQueryHandler(
                    skip_process_major,
                    pattern="^(skipmaj_|skip_change_major$|skip_cancel$)"
                )
            ],
            SKIP_SUBJECT: [
                CallbackQueryHandler(
                    skip_process_subject,
                    pattern="^(skipsub_|skip_change_major$|skip_cancel$)"
                )
            ],
            SKIP_MISSED: [MessageHandler(filters.TEXT & ~filters.COMMAND, skip_receive_missed)]
        },
        fallbacks=[CommandHandler('cancel', skip_cancel_cmd)]
    )
    app.add_handler(skip_conv)

    for major in list(MAJORS) + ["it", "timetable", "tt"]:
        app.add_handler(CommandHandler(major, get_timetable_cmd))

    # ================= STANDARD COMMANDS & HANDLERS =================
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CallbackQueryHandler(menu_handler, pattern="^(menu_|back_to_main)"))

    # 📌 Multi-Major & Dynamic Semester Management
    app.add_handler(CommandHandler("setmajor", setmajor_cmd))
    app.add_handler(CommandHandler("setsemester", setsemester_cmd))
    app.add_handler(CallbackQueryHandler(setsemester_callback, pattern="^semcfg_"))
    app.add_handler(CommandHandler("setgroupmajor", setgroupmajor_cmd))
    app.add_handler(CommandHandler("reconcilerolls", reconcilerolls_cmd))
    app.add_handler(CommandHandler("forceroll", force_roll_cmd))
    app.add_handler(CommandHandler("addec", addec_cmd))
    app.add_handler(CommandHandler("delec", delec_cmd))
    app.add_handler(CommandHandler("eclist", eclist_cmd))

    app.add_handler(CommandHandler("setcooldown", set_cooldown_cmd))
    app.add_handler(CallbackQueryHandler(set_cooldown_action, pattern="^setcd_"))
    app.add_handler(CommandHandler("setusercd", set_user_cooldown_cmd))
    app.add_handler(CommandHandler("setlimit", set_daily_limit_cmd))
    app.add_handler(CallbackQueryHandler(admin_approval_action, pattern="^(aprv_|rjct_)"))

    app.add_handler(CommandHandler('creator', show_creator))
    app.add_handler(CommandHandler('status', show_status))
    app.add_handler(CommandHandler('wifi', show_wifi))
    app.add_handler(CallbackQueryHandler(paginate_wifi, pattern="^wifi_page_"))

    app.add_handler(CommandHandler('addwifi', add_wifi))
    app.add_handler(CommandHandler('delwifi', del_wifi))
    app.add_handler(CommandHandler('addegg', add_egg))
    app.add_handler(CommandHandler('delegg', del_egg))
    app.add_handler(CommandHandler("post999", post999_cmd))
    app.add_handler(CommandHandler("resetnick", reset_nickname_quota_cmd))

    app.add_handler(CommandHandler('attendance', start_attendance_cmd))
    app.add_handler(CommandHandler('dates', view_dates_cmd))
    app.add_handler(CallbackQueryHandler(close_attendance_action, pattern="^att_close_"))
    app.add_handler(CallbackQueryHandler(dates_callback_handler, pattern="^vdate_"))

    app.add_handler(CommandHandler('routes', route_cmd))
    app.add_handler(CallbackQueryHandler(route_action, pattern="^route_"))

    app.add_handler(CommandHandler(['encrypt', 'decrypt'], encrypt_decrypt))
    app.add_handler(CommandHandler('bin2hex', bin_to_hex))
    app.add_handler(CommandHandler('hex2bin', hex_to_bin))

    app.add_handler(CommandHandler(['addcmd1', 'addcmd2', 'addcmd3'], add_custom_cmd))
    app.add_handler(CommandHandler('delcmd', del_custom_cmd))
    app.add_handler(MessageHandler(filters.COMMAND, handle_custom_cmd), group=2)

    app.add_handler(CommandHandler("approvers", approvers_cmd))
    app.add_handler(CommandHandler("import", import_members))
    app.add_handler(CommandHandler("mention", mention_all))
    app.add_handler(CommandHandler("addclass", add_class))
    app.add_handler(CommandHandler("delclass", del_class))
    app.add_handler(CommandHandler("clearday", clear_day))
    app.add_handler(CommandHandler("autott_off", autott_off_cmd))
    app.add_handler(CommandHandler("autott_on", autott_on_cmd))
    app.add_handler(CommandHandler("autott_status", autott_status_cmd))

    app.add_handler(CommandHandler('weather', weather_cmd))
    app.add_handler(CommandHandler('tasks', show_tasks))
    app.add_handler(CommandHandler('tutorials', show_tasks))

    app.add_handler(CommandHandler('adddummy', add_dummy_data))
    app.add_handler(InlineQueryHandler(inline_search))

    app.add_handler(CommandHandler('newfund', newfund_cmd))
    app.add_handler(CommandHandler('pay', payfund_cmd))
    app.add_handler(CommandHandler('funds', funds_cmd))
    app.add_handler(CallbackQueryHandler(fund_flow_callback, pattern=r'^fundflow:'))
    app.add_handler(CallbackQueryHandler(fund_manage_callback, pattern=r'^fundmanage:'))
    # Handles only text messages while a fund/payment wizard is waiting for input.
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, fund_flow_text_handler), group=1)
    # Fund deletion is intentionally handled as a soft-cancel from /funds.
    app.add_handler(CommandHandler('setphone', setphone_cmd))
    app.add_handler(CommandHandler('settg', settg_cmd))
    app.add_handler(CommandHandler('undo', undo_cmd))
    app.add_handler(CommandHandler("history", history_cmd))
    app.add_handler(CommandHandler(['add', 'ph'], user_register_legacy_redirect))
    app.add_handler(CommandHandler('myprofile', user_myprofile_cmd))

    app.add_handler(CommandHandler("allocate", allocate_fund))
    app.add_handler(CommandHandler("ecstat", ecstat_cmd))
    app.add_handler(CallbackQueryHandler(ecstat_callback, pattern=r'^ecstat:'))
    app.add_handler(CommandHandler("export_finance", export_finance_report))

    app.add_handler(CommandHandler("assign_ec", assign_ec_cmd))
    app.add_handler(CommandHandler("archive_fund", archive_fund_cmd))
    app.add_handler(CommandHandler("unarchive_fund", unarchive_fund_cmd))
    app.add_handler(CommandHandler("fundstat", fundstat_cmd))

    app.add_handler(CommandHandler("spend", spend_cmd))
    app.add_handler(CommandHandler("me", ec_me))
    app.add_handler(CommandHandler("nuke", nuke_funds_cmd))
    app.add_handler(CallbackQueryHandler(nuke_approval_callback, pattern=r"^nuke:"))
    app.add_handler(CallbackQueryHandler(fund_selection_callback, pattern=r"^finpick:"))

    app.add_handler(CommandHandler('list', list_cmd))
    app.add_handler(CommandHandler('report', report_cmd))
    app.add_handler(CommandHandler('vault', vault_cmd))
    app.add_handler(CommandHandler('hostel', hostel_cmd))
    app.add_handler(CommandHandler('export', export_cmd))
    app.add_handler(CallbackQueryHandler(export_callback, pattern=r'^exportpick:'))
    app.add_handler(CommandHandler('semhistory', semhistory_cmd))
    app.add_handler(CallbackQueryHandler(semhistory_callback, pattern=r'^semhist:'))
    app.add_handler(CommandHandler('exportfund', export_fund_cmd))
    app.add_handler(CommandHandler('remind', remind_cmd))

    app.add_handler(CommandHandler('endsemester', end_semester_cmd))

    for major in MAJORS:
        app.add_handler(CommandHandler(major, get_timetable_cmd))

    app.add_handler(CallbackQueryHandler(change_timetable_day, pattern="^tt_"))