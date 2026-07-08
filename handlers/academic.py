import os
import logging
import asyncio
import random
from datetime import datetime, timedelta, timezone
from telegram.ext import ConversationHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from database.db import get_db

logger = logging.getLogger(__name__)
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
DAYS_OF_WEEK = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
MAJORS = ["it", "mc", "archi", "ep", "ec", "me", "civil"] # Routing အတွက် Default List
T_SUBJECT, T_DATE, T_CONTENT = range(70, 73)
SUBJECTS_LIST = ["Myanmar", "English", "Maths", "Physics", "CEIT", "Workshop"]

# ================= 🛡️ Admin & Helpers =================
async def is_admin_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id
    if user_id == OWNER_ID: return True
    if update.effective_chat.type in ['group', 'supergroup']:
        try:
            admins = await context.bot.get_chat_administrators(update.effective_chat.id)
            admin_ids = [admin.user.id for admin in admins]
            if user_id in admin_ids: return True
        except: pass
    return False

def get_class_groups():
    group_id_str = os.getenv("CLASS_GROUP_ID", "0")
    try:
        return [int(g.strip()) for g in group_id_str.split(",") if g.strip() and g.strip() != "0"]
    except ValueError:
        logger.error("CLASS_GROUP_ID format မှားယွင်းနေပါသည်။")
        return []

# ================= 🗑 Auto Delete Helpers =================
background_tasks = set()

async def delayed_delete_task(bot, chat_id, msg_ids, delay):
    await asyncio.sleep(delay)
    for msg_id in msg_ids:
        try: await bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except: pass

def schedule_academic_delete(context, chat_id, msg_ids, delay=60):
    task = asyncio.create_task(delayed_delete_task(context.bot, chat_id, msg_ids, delay))
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)

# ================= 🔔 ၁၀ မိနစ်ကြိုတင် သတိပေးစနစ် (ROUTING SYSTEM ပါဝင်သည်) =================
async def class_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    db = get_db()
    if db is None: return

    mm_tz = timezone(timedelta(hours=6, minutes=30))
    now = datetime.now(mm_tz)
    if now.weekday() > 4: return
    current_day = DAYS_OF_WEEK[now.weekday()]

    target_time = now + timedelta(minutes=10)
    target_time_str = target_time.strftime("%H:%M")

    cursor = db.timetable.find({"day": current_day})
    records = await cursor.to_list(length=None)
    if not records: return

    group_ids = get_class_groups()
    if not group_ids: return

    # 📌 Group အလိုက် ခွင့်ပြုထားသော Major များကို DB မှ ကြိုဆွဲထားပါမည်
    group_routes = {}
    for gid in group_ids:
        doc = await db.group_routes.find_one({"group_id": gid})
        # Database တွင် မရှိသေးပါက Default အနေဖြင့် Major အကုန်လုံးကို ဖွင့်ပေးထားမည်
        group_routes[gid] = doc.get("majors", MAJORS) if doc else MAJORS

    reminders_to_send = {}
    
    for record in records:
        major_name = record.get("major", "ALL").upper()
        if "periods" not in record: continue
        
        for p in record["periods"]:
            start_time = p["time"].split("-")[0].strip()
            if start_time == target_time_str:
                key = (p["subject"], p["teacher"], p["room"])
                if key not in reminders_to_send: reminders_to_send[key] = []
                if major_name not in reminders_to_send[key]: reminders_to_send[key].append(major_name)

    for (subject, teacher, room), class_majors in reminders_to_send.items():
        tag = " <code>[ALL]</code>" if "ALL" in class_majors else f" <code>[{', '.join(class_majors)}]</code>"
        msg = f"🔔 <b>အတန်းစရန် (၁၀) မိနစ်သာ လိုပါတော့သည်!</b>\n\n📚 <b>ဘာသာရပ်:</b> {subject}{tag}\n👨‍🏫 <b>ဆရာ/မ:</b> {teacher}\n🏫 <b>အခန်း:</b> {room}"
        
        for gid in group_ids:
            allowed_majors = group_routes.get(gid, [])
            should_send = False
            
            # 📌 အတန်းချိန်သည် ALL ဖြစ်လျှင် သို့မဟုတ် Group ၏ ခွင့်ပြုစာရင်းထဲတွင် ထို Major ပါဝင်လျှင် ပို့မည်
            if "ALL" in class_majors:
                should_send = True
            else:
                for m in class_majors:
                    if m.lower() in allowed_majors:
                        should_send = True
                        break
            
            if should_send:
                try:
                    sent_msg = await context.bot.send_message(chat_id=gid, text=msg, parse_mode=ParseMode.HTML)
                    schedule_academic_delete(context, gid, [sent_msg.message_id], 600)
                except: pass

# ================= 📅 TIMETABLE VIEW =================
async def build_timetable_message(major: str, day_idx: int):
    day_name = DAYS_OF_WEEK[day_idx]
    db = get_db()
    
    text = f"📅 <b>{major.upper()} TIMETABLE - {day_name}</b>\n━━━━━━━━━━━━━━━━━━\n"
    has_class = False
    
    if db is not None:
        cursor = db.timetable.find({
            "day": day_name, 
            "major": {"$in": [major.lower(), "all"]}
        })
        records = await cursor.to_list(length=None)
        
        all_periods = []
        for record in records:
            if "periods" in record:
                all_periods.extend(record["periods"])
                
        all_periods.sort(key=lambda x: x["time"])

        for p in all_periods:
            has_class = True
            text += f"⏰ <b>{p['time']}</b>\n"
            text += f"📚 Subject: {p['subject']}\n"
            text += f"👨‍🏫 Teacher: {p['teacher']}\n"
            text += f"🏫 Room: {p['room']}\n\n"
                
    if not has_class:
        text += "🎉 <i>ဒီနေ့အတွက် အတန်းမရှိပါ။ (Free Day)</i>\n"

    prev_idx = (day_idx - 1) % 5
    next_idx = (day_idx + 1) % 5
    keyboard = [[
        InlineKeyboardButton("⬅️", callback_data=f"tt_{major}_{prev_idx}"),
        InlineKeyboardButton(day_name[:3], callback_data="ignore"),
        InlineKeyboardButton("➡️", callback_data=f"tt_{major}_{next_idx}")
    ]]
    return text, InlineKeyboardMarkup(keyboard)

async def get_timetable_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_msg_id = update.message.message_id
    
    cmd = update.message.text.split()[0].lower().replace('/', '')
    major = cmd.upper()
    
    mm_tz = timezone(timedelta(hours=6, minutes=30))
    now = datetime.now(mm_tz)
    
    day_idx = now.weekday()
    
    if now.hour >= 16:
        day_idx += 1
        
    if day_idx > 4:
        day_idx = 0
    
    text, markup = await build_timetable_message(major, day_idx)
    sent = await update.message.reply_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
    schedule_academic_delete(context, chat_id, [user_msg_id, sent.message_id], 60)

async def change_timetable_day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.data == "ignore": return await query.answer()
    
    _, major, day_idx_str = query.data.split('_')
    day_idx = int(day_idx_str)
    
    text, markup = await build_timetable_message(major, day_idx)
    try:
        await query.edit_message_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
    except: pass
    await query.answer()

# ================= 📝 TASKS & TUTORIALS VIEW =================
async def show_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_msg_id = update.message.message_id
    cmd = update.message.text.split()[0].lower()
    task_type = "tutorial" if "tutorial" in cmd else "task"
    
    db = get_db()
    if db is None: return
    
    mm_tz = timezone(timedelta(hours=6, minutes=30))
    now = datetime.now(mm_tz)
    
    # 📌 Archive စနစ်: Date ကျော်သွားတာတွေကို အလိုလို ဖျောက်ထားမည်
    cursor = db.tasks.find({"type": task_type, "target_dt": {"$gte": now}}).sort("target_dt", 1)
    tasks = await cursor.to_list(length=100)
    
    title = "📚 <b>TUTORIALS & EXAMS</b>" if task_type == "tutorial" else "📝 <b>ASSIGNMENTS & TASKS</b>"
    
    if not tasks:
        msg = f"{title}\n━━━━━━━━━━━━━━━━━━\n🎉 <b>ဘာမှမရှိသေးဘူး။ ဘာလဲ စာလုပ်ချင်လို့လား။</b>"
        sent = await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
        schedule_academic_delete(context, chat_id, [user_msg_id, sent.message_id], 60)
        return
        
    # ရက်စွဲအလိုက် အုပ်စုဖွဲ့ပြသခြင်း
    grouped = {}
    for t in tasks:
        d_str = t.get('date_str', 'Unknown Date')
        if d_str not in grouped: grouped[d_str] = []
        grouped[d_str].append(t)
        
    msg = f"{title}\n━━━━━━━━━━━━━━━━━━\n"
    for d_str, items in grouped.items():
        msg += f"📅 <b>{d_str} (မနက် ၉:၀၀ နာရီ)</b>\n"
        for t in items:
            msg += f"  🔹 <code>[ID:{t.get('short_id')}]</code> <b>{t.get('subject')}</b>\n"
            msg += f"      👉 <i>{t.get('content')}</i>\n"
        msg += "\n"
        
    sent = await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
    schedule_academic_delete(context, chat_id, [user_msg_id, sent.message_id], 120)


# ================= 📝 TASKS & TUTORIALS (ADD & EDIT) =================
async def add_task_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ယခင် add_task နေရာတွင် အစားထိုးရန်"""
    if not await is_admin_check(update, context): return ConversationHandler.END

    cmd = update.message.text.split()[0].lower()
    
    if "edit" in cmd:
        if len(context.args) < 1:
            await update.message.reply_text(f"⚠️ အသုံးပြုနည်း: <code>{cmd} &lt;ID&gt;</code>", parse_mode=ParseMode.HTML)
            return ConversationHandler.END
        try:
            task_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("❌ ID သည် ဂဏန်းဖြစ်ရမည်။")
            return ConversationHandler.END
            
        db = get_db()
        task = await db.tasks.find_one({"short_id": task_id})
        if not task:
            await update.message.reply_text("❌ ထို ID ဖြင့် ရှာမတွေ့ပါ။")
            return ConversationHandler.END
            
        context.user_data['t_edit_id'] = task_id
        context.user_data['t_type'] = task.get("type", "task")
        txt = f"✏️ <b>ID: {task_id} အား ပြင်ဆင်ခြင်း</b>"
    else:
        context.user_data['t_type'] = "tutorial" if "tutorial" in cmd else "task"
        context.user_data.pop('t_edit_id', None)
        txt = "🎯 <b>အသစ်ထည့်သွင်းခြင်း</b>"

    # ဘာသာရပ်ရွေးရန် Inline Buttons
    keyboard = []
    for i in range(0, len(SUBJECTS_LIST), 2):
        row = [InlineKeyboardButton(s, callback_data=f"tsubj_{s}") for s in SUBJECTS_LIST[i:i+2]]
        keyboard.append(row)
        
    await update.message.reply_text(f"{txt}\n\nဘာသာရပ်ကို ရွေးချယ်ပါ -", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
    return T_SUBJECT

async def t_subject_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    subject = query.data.split("_")[1]
    context.user_data['t_subject'] = subject
    await query.edit_message_text(f"✅ ဘာသာရပ်: <b>{subject}</b>\n\n📅 <b>ရက်စွဲထည့်ပါ။</b> (ဥပမာ - <code>28/06/2026</code>)", parse_mode=ParseMode.HTML)
    return T_DATE

async def t_date_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    date_str = update.message.text.strip()
    mm_tz = timezone(timedelta(hours=6, minutes=30))
    try:
        dt = datetime.strptime(date_str, "%d/%m/%Y")
        target_dt = dt.replace(hour=9, minute=0, second=0, tzinfo=mm_tz)
        
        if target_dt < datetime.now(mm_tz):
            await update.message.reply_text("❌ အတိတ်က ရက်စွဲများ ထည့်၍မရပါ။ အသစ်ထပ်ထည့်ပါ။")
            return T_DATE
            
        context.user_data['t_date_str'] = date_str
        context.user_data['t_target_dt'] = target_dt
        await update.message.reply_text(f"✅ ရက်စွဲ: <b>{date_str}</b>\n\n📝 <b>အကြောင်းအရာ (Content) ကို ရိုက်ထည့်ပါ။</b>", parse_mode=ParseMode.HTML)
        return T_CONTENT
    except ValueError:
        await update.message.reply_text("❌ Format မှားနေပါသည်။ <code>DD/MM/YYYY</code> ပုံစံဖြင့် ထပ်ရိုက်ပါ။", parse_mode=ParseMode.HTML)
        return T_DATE

async def t_content_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    content = update.message.text.strip()
    task_type = context.user_data.get('t_type')
    subject = context.user_data.get('t_subject')
    date_str = context.user_data.get('t_date_str')
    target_dt = context.user_data.get('t_target_dt')
    edit_id = context.user_data.get('t_edit_id')
    
    db = get_db()
    if edit_id:
        await db.tasks.update_one(
            {"short_id": edit_id},
            {"$set": {"subject": subject, "date_str": date_str, "target_dt": target_dt, "content": content, "reminded": False}}
        )
        msg = f"✅ <b>[ID: {edit_id}] ပြင်ဆင်ပြီးပါပြီ။</b>"
    else:
        short_id = random.randint(1000, 9999)
        await db.tasks.insert_one({
            "short_id": short_id, "type": task_type, "subject": subject,
            "date_str": date_str, "target_dt": target_dt, "content": content, "reminded": False
        })
        msg = f"✅ <b>အောင်မြင်ပါသည်။</b> (ID: <code>{short_id}</code>)"

    await update.message.reply_text(f"{msg}\n\n📚 {subject}\n📅 {date_str}\n📝 {content}", parse_mode=ParseMode.HTML)
    context.user_data.clear()
    return ConversationHandler.END


# ================= 🔔 TASKS & TUTORIALS REMINDER JOB =================
async def tasks_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    """(၁) နာရီတစ်ခါ အလုပ်လုပ်ပြီး Tutorial/Task များကို ကြိုတင်သတိပေးမည်"""
    db = get_db()
    if db is None: return

    mm_tz = timezone(timedelta(hours=6, minutes=30))
    now = datetime.now(mm_tz)
    group_ids = get_class_groups()
    if not group_ids: return
    
    # 📌 ၃ ခုမြောက် Group (CEIT major group) သို့သာ ပို့ရန် (Index 2)
    # တကယ်လို့ Group ID က ၃ ခု မပြည့်ရင် နောက်ဆုံး Group ကို ယူမည်
    main_group_id = group_ids[2] if len(group_ids) >= 3 else group_ids[-1]

    tasks = await db.tasks.find({"reminded": False}).to_list(length=None)
    
    for t in tasks:
        dt_target = t.get("target_dt")
        if not dt_target: continue
        
        dt_target = dt_target.replace(tzinfo=timezone.utc).astimezone(mm_tz)
        task_type = t.get("type", "task")
        
        remind_time = dt_target - timedelta(days=3) if task_type == "tutorial" else dt_target - timedelta(hours=18)

        if now >= remind_time and now < dt_target:
            emoji = "📚" if task_type == "tutorial" else "📝"
            title = "TUTORIAL REMINDER" if task_type == "tutorial" else "ASSIGNMENT REMINDER"
            
            msg = (
                f"🔔 <b>{title}</b>\n━━━━━━━━━━━━━━━━━━\n"
                f"{emoji} <b>ဘာသာရပ်:</b> {t.get('subject')}\n"
                f"📅 <b>နောက်ဆုံးရက်:</b> <code>{t.get('date_str')}</code> (မနက် ၉:၀၀ နာရီ)\n\n"
                f"📖 <b>အကြောင်းအရာ:</b>\n{t.get('content')}\n\n"
                f"<i>(ကြိုတင်ပြင်ဆင်နိုင်ရန် အလိုအလျောက် သတိပေးခြင်း ဖြစ်ပါသည်။)</i>"
            )
            
            try:
                await context.bot.send_message(chat_id=main_group_id, text=msg, parse_mode=ParseMode.HTML)
                await db.tasks.update_one({"_id": t["_id"]}, {"$set": {"reminded": True}})
            except: pass


# ================= ⚙️ ADMIN MANAGEMENT =================
async def add_class(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin_check(update, context): return
    args = context.args
    if len(args) < 6: return await update.message.reply_text("⚠️ `/addclass <Day> <Time> <Major> <Room> <Teacher> <Subject>`", parse_mode=ParseMode.HTML)
    
    day, time, major, room, teacher, subject = args[0], args[1], args[2], args[3], args[4], " ".join(args[5:])
    db = get_db()
    if db is None: return
    
    period = {"time": time, "room": room, "teacher": teacher, "subject": subject}
    await db.timetable.update_one(
        {"day": day, "major": major.lower()}, 
        {"$push": {"periods": period}}, 
        upsert=True
    )
    await update.message.reply_text(f"✅ {day} တွင် <b>{major.upper()}</b> အတွက် အတန်းချိန်အသစ် သွင်းပြီးပါပြီ。", parse_mode=ParseMode.HTML)

async def del_class(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin_check(update, context): return
    args = context.args
    if len(args) < 3: return await update.message.reply_text("⚠️ `/delclass <Day> <Time> <Major>`", parse_mode=ParseMode.HTML)
    
    db = get_db()
    if db is None: return
    
    await db.timetable.update_one(
        {"day": args[0], "major": args[2].lower()}, 
        {"$pull": {"periods": {"time": args[1]}}}
    )
    await update.message.reply_text(f"🗑️ {args[0]} မှ {args[1]} ({args[2].upper()}) အတန်းကို ဖျက်လိုက်ပါပြီ။")

async def clear_day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin_check(update, context): return
    if len(context.args) < 2: return await update.message.reply_text("⚠️ အသုံးပြုနည်း: `/clearday <Day> <Major>`", parse_mode=ParseMode.HTML)
    
    day = context.args[0]
    major = context.args[1]
    
    db = get_db()
    if db is None: return
    
    await db.timetable.update_one(
        {"day": day, "major": major.lower()}, 
        {"$set": {"periods": []}}
    )
    await update.message.reply_text(f"🧹 {day} ၏ <b>{major.upper()}</b> အတန်းချိန်အားလုံးကို ရှင်းလင်းလိုက်ပါပြီ。", parse_mode=ParseMode.HTML)