import csv
import io
from datetime import datetime, timezone, timedelta
from telegram import Update, InputFile
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

# ⚠️ မိမိတို့ Project ၏ Database နှင့် Auth စနစ်ကို ဤနေရာတွင် မှန်ကန်စွာ Import လုပ်ရန် လိုအပ်ပါသည်
from database.db import get_db 
from utils.auth import is_super_admin, is_owner

# ================= 📌 EC Mapping (Constants) =================
# EC များ၏ Telegram User ID များကို ဤနေရာတွင် ကြိုတင်သတ်မှတ်ထားပါ
EC_MAP = {
    "ben": 6598291638,  # User ID အမှန်များကို ပြင်ထည့်ပါ
    "chan": 1898170145, 
    "jojo": 6948534484    
}

# ================= 💰 1. EC ထံ ငွေလွှဲပေးခြင်း (/allocate) =================
async def allocate_fund(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_super_admin(update.effective_user.id): return
    
    if len(context.args) < 2:
        return await update.message.reply_text("⚠️ <b>အသုံးပြုနည်း:</b> <code>/allocate @username ပမာဏ</code>", parse_mode=ParseMode.HTML)
        
    ec_username = context.args[0].replace("@", "")
    try:
        amount = int(context.args[1])
    except ValueError:
        return await update.message.reply_text("⚠️ ပမာဏကို ဂဏန်းဖြင့်သာ ထည့်ပါ။")

    db = get_db()
    
    await db.ec_ledgers.update_one(
        {"username": ec_username},
        {
            "$inc": {"total_received": amount, "current_balance": amount},
            "$setOnInsert": {"total_spent": 0, "expense_history": []}
        },
        upsert=True
    )
    
    await update.message.reply_text(f"✅ <b>EC @{ec_username} ထံသို့ {amount} MMK လွှဲပြောင်းပေးပြီးပါပြီ။</b>", parse_mode=ParseMode.HTML)

# ================= 💸 EC ငွေသုံးစွဲမှု ရေးသွင်းခြင်း (/spend) =================
async def spend_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username
    db = get_db()
    
    # 1. ရိုက်ထည့်လိုက်သော Command Format စစ်ဆေးခြင်း (/spend <amount> <reason>)
    if not context.args or len(context.args) < 2:
        return await update.message.reply_text(
            "⚠️ <b>အသုံးပြုနည်း:</b> <code>/spend <ပမာဏ> <အကြောင်းအရာ></code>\n"
            "<i>ဥပမာ: /spend 38000 ဝါဆိုကြေး</i>", 
            parse_mode=ParseMode.HTML
        )
        
    try:
        spend_amount = int(context.args[0])
        reason = " ".join(context.args[1:])
    except ValueError:
        return await update.message.reply_text("⚠️ ကျေးဇူးပြု၍ ပမာဏကို ဂဏန်းသီးသန့် ရိုက်ထည့်ပါ။")

    # 2. Archive ဖြစ်နေသော Fund များကို DB မှ ဆွဲထုတ်ခြင်း
    all_funds = await db.funds.find({}).to_list(length=None)
    archived_fund_ids = set([f["fund_id"].lower() for f in all_funds if f.get("status") == "archived"])
    
    sys_config = await db.sys_config.find_one({"type": "archived_funds"})
    if sys_config:
        archived_fund_ids.update([f.lower() for f in sys_config.get("funds", [])])

    # 3. EC တာဝန်ယူထားသော ကျောင်းသားများဆီမှ Active ရငွေ စုစုပေါင်း တွက်ချက်ခြင်း
    assigned_students = await db.student_info.find({
        "$or": [
            {"assigned_ec_id": user_id},
            {"tg_id": user_id}
        ]
    }).to_list(length=None)
    
    active_total_collected = 0
    for st in assigned_students:
        paid_funds = st.get("paid_funds", {})
        for f_id, f_data in paid_funds.items():
            amount = f_data.get("amount", 0) if isinstance(f_data, dict) else f_data
            if amount > 0 and f_id.lower() not in archived_fund_ids:
                active_total_collected += amount

    # 4. အရင်က သုံးစွဲထားသော စုစုပေါင်းငွေ ဆွဲထုတ်ခြင်း
    ec_ledger = await db.ec_ledgers.find_one({"username": username.lower()}) or {}
    total_spent = ec_ledger.get("total_spent", 0)
    
    # 5. လက်ရှိ အမှန်တကယ် လက်ထဲတွင် ကျန်သောငွေ (Net Balance)
    current_balance = active_total_collected - total_spent

    # 6. ပိုက်ဆံ လုံလောက်မှု ရှိ/မရှိ စစ်ဆေးခြင်း
    if spend_amount > current_balance:
        return await update.message.reply_text(
            f"❌ <b>ငွေမလုံလောက်ပါ။</b>\n\n"
            f"💰 လက်ရှိသုံးစွဲနိုင်သော လက်ကျန်ငွေ: <b>{current_balance:,} MMK</b>\n"
            f"💸 သုံးစွဲလိုသော ပမာဏ: <b>{spend_amount:,} MMK</b>\n"
            f"<i>(မှတ်ချက်: Archive လုပ်ထားသော ငွေများကို သုံးစွဲ၍ မရပါ)</i>",
            parse_mode=ParseMode.HTML
        )

    # 7. ငွေစာရင်း လုံလောက်ပါက Expense Log သို့ သွားပေါင်းမည်
    new_total_spent = total_spent + spend_amount
    
    # ⚠️ ပြင်ဆင်ချက်: username အစား ec_id (user_id) ဖြင့် တိုက်ရိုက်ရှာဖွေ/သိမ်းဆည်းမည်
    await db.ec_ledgers.update_one(
        {"ec_id": user_id}, 
        {
            "$set": {
                "username": username.lower(), # Username ကို မှတ်တမ်းအနေနဲ့သာ ထားမည်
                "total_spent": new_total_spent
            },
            "$push": {
                "expenses": {
                    "amount": spend_amount,
                    "reason": reason,
                    "date": update.message.date.strftime("%Y-%m-%d %H:%M")
                }
            }
        },
        upsert=True
    )

    # 8. အောင်မြင်ကြောင်း မက်ဆေ့ခ်ျ ပို့ခြင်း
    remaining = current_balance - spend_amount
    await update.message.reply_text(
        f"✅ <b>သုံးစွဲမှု မှတ်တမ်းတင်ပြီးပါပြီ!</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👤 EC: @{username}\n"
        f"💸 သုံးစွဲငွေ: <b>{spend_amount:,} MMK</b>\n"
        f"📝 အကြောင်းအရာ: <b>{reason}</b>\n"
        f"💰 လက်ထဲတွင် ကျန်ငွေ: <b>{remaining:,} MMK</b>",
        parse_mode=ParseMode.HTML
    )
    

# ================= 👤 Admin သီးသန့်: အခြား EC များ၏ စာရင်းကြည့်ရန် (/ecstat) =================
async def ecstat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.auth import is_super_admin
    if not is_super_admin(update.effective_user.id): return
    
    if len(context.args) < 1:
        return await update.message.reply_text("⚠️ <b>အသုံးပြုနည်း:</b> <code>/ecstat @username</code> သို့မဟုတ် <code>/ecstat nickname</code>\n(ဥပမာ - /ecstat jojo)", parse_mode=ParseMode.HTML)
        
    target_name = context.args[0].replace("@", "").lower()
    db = get_db()
    
    # 🕵️‍♂️ ၁။ Target EC ၏ Telegram ID ကို ရှာဖွေခြင်း
    target_user_id = None
    target_identifiers = [target_name]
    
    ledger = await db.ec_ledgers.find_one({"username": target_name})
    if ledger and "ec_id" in ledger:
        target_user_id = ledger["ec_id"]
        target_identifiers.append(str(target_user_id))
        
    try:
        for nick, tg_id in EC_MAP.items():
            if nick.lower() == target_name:
                target_user_id = tg_id
                target_identifiers.append(str(tg_id))
                break
    except Exception:
        pass

    # 🔥 [ THE MAGIC FIX ] Telegram API ကိုလှမ်းခေါ်ပြီး Real Username ကို Auto ဆွဲထုတ်မည်
    if target_user_id:
        try:
            chat_info = await context.bot.get_chat(target_user_id)
            if chat_info.username:
                target_identifiers.append(chat_info.username.lower())
        except Exception:
            pass

    # 🕵️‍♂️ ၂။ Fund အချက်အလက်များ ယူမည်
    all_funds = await db.funds.find({}).to_list(length=None)
    
    archived_fund_ids = set()
    fund_owners = {}
    fund_name_map = {}
    
    for f in all_funds:
        f_id = f["fund_id"].lower()
        fund_name_map[f_id] = f.get("fund_name", f["fund_id"])
        
        if f.get("status") == "archived":
            archived_fund_ids.add(f_id)
            
        manager = f.get("managed_by")
        if manager:
            fund_owners[f_id] = manager.lower()
            
    sys_config = await db.sys_config.find_one({"type": "archived_funds"})
    if sys_config:
        for f in sys_config.get("funds", []):
            archived_fund_ids.add(f.lower())

    # 🌐 ၃။ ကျောင်းသား အားလုံး ကို ဆွဲထုတ်မည်
    all_students = await db.student_info.find({}).to_list(length=None)
    
    assigned_students_count = 0
    active_fund_totals = {}
    archived_fund_totals = {}
    grand_total_collected = 0
    
    # ⚙️ ၄။ ငွေများကို ပစ်မှတ်ထားသော Target EC ထံ လမ်းကြောင်းခွဲခြင်း
    for st in all_students:
        is_target_student = False
        
        if st.get("assigned_ec") == target_name or st.get("assigned_ec_id") == target_name:
            is_target_student = True
        elif target_user_id and (st.get("assigned_ec_id") == target_user_id or st.get("tg_id") == target_user_id):
            is_target_student = True
            
        if is_target_student:
            assigned_students_count += 1
            
        paid_funds = st.get("paid_funds", {})
        for f_id, f_data in paid_funds.items():
            amount = f_data.get("amount", 0) if isinstance(f_data, dict) else f_data
            
            if amount > 0:
                clean_f_id = f_id.lower()
                fund_manager = fund_owners.get(clean_f_id, "ec") 
                
                is_target_money = False
                
                if fund_manager == "ec" and is_target_student:
                    is_target_money = True
                elif fund_manager in target_identifiers:
                    is_target_money = True
                    
                if is_target_money:
                    if clean_f_id in archived_fund_ids:
                        archived_fund_totals[clean_f_id] = archived_fund_totals.get(clean_f_id, 0) + amount
                    else:
                        active_fund_totals[clean_f_id] = active_fund_totals.get(clean_f_id, 0) + amount
                        grand_total_collected += amount
            
    # 💸 ၅။ သုံးစွဲမှု (Ledger) တွက်ချက်ခြင်း (ယခုအခါ bella_ellen အမည်ဖြင့် ပါရှာသွားပါမည်)
    ec_ledger = await db.ec_ledgers.find_one({"username": {"$in": target_identifiers}}) or {}
    total_spent = ec_ledger.get("total_spent", 0)
    net_in_hand = grand_total_collected - total_spent
    
    if assigned_students_count == 0 and grand_total_collected == 0 and total_spent == 0 and not archived_fund_totals:
        return await update.message.reply_text(f"⚠️ <b>{target_name}</b> ၏ စာရင်း မတွေ့ပါ (သို့) ရငွေ/သုံးငွေ မှတ်တမ်း မရှိသေးပါ။", parse_mode=ParseMode.HTML)

    # 💬 ၆။ Message တည်ဆောက်ခြင်း
    msg = f"📊 <b>EC DASHBOARD - @{target_name} (Viewed by Admin)</b>\n"
    msg += f"━━━━━━━━━━━━━━━━━━\n"
    msg += f"👥 တာဝန်ယူထားသော ကျောင်းသား: <b>{assigned_students_count}</b> ဦး\n\n"
    
    msg += f"<b>[ 🎯 လက်ရှိ ကောက်ခံရရှိမှု (Active) ]</b>\n"
    if active_fund_totals:
        for f_id, f_amt in active_fund_totals.items():
            display_name = fund_name_map.get(f_id, f_id)
            msg += f"• {display_name}: <b>{f_amt:,} MMK</b>\n"
    else:
        msg += "<i>လက်ရှိ ကောက်ခံထားသော ငွေမရှိသေးပါ</i>\n"
        
    msg += f"📥 <b>စုစုပေါင်း ရငွေ: {grand_total_collected:,} MMK</b>\n\n"
    
    msg += f"<b>[ 💸 သုံးစွဲမှုနှင့် လက်ကျန် ]</b>\n"
    msg += f"📤 သုံးစွဲပြီးငွေ: <b>{total_spent:,} MMK</b>\n"
    msg += f"💰 <b>လက်ထဲတွင် ကျန်ငွေ: {net_in_hand:,} MMK</b>\n"
    
    if archived_fund_totals:
        msg += f"\n📦 <b>[ ဆရာမထံ အပ်နှံပြီးသော ရန်ပုံငွေများ ]</b>\n"
        for f_id, f_amt in archived_fund_totals.items():
            display_name = fund_name_map.get(f_id, f_id)
            msg += f"• {display_name}: <b>{f_amt:,} MMK</b>\n"
            
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

# ================= 📌 4. Dynamic EC Assign (/assign_ec) =================
async def assign_ec_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.auth import is_super_admin
    
    if not is_super_admin(update.effective_user.id): return
    
    if len(context.args) < 2:
        return await update.message.reply_text("⚠️ <b>အသုံးပြုနည်း:</b> <code>/assign_ec [nickname] [roll1] [roll2] ...</code>", parse_mode=ParseMode.HTML)
        
    nickname = context.args[0].lower()
    if nickname not in EC_MAP:
        return await update.message.reply_text(f"❌ EC Nickname '<b>{nickname}</b>' မတွေ့ပါ။ (ben, chan, jo သာ ရမည်)", parse_mode=ParseMode.HTML)
        
    ec_id = EC_MAP[nickname]
    raw_rolls = context.args[1:]
    
    # ⚠️ အကြမ်းစား Regex Logic ဖြင့် String ထဲမှ ဖမ်းခြင်း
    or_conditions = []
    
    for r in raw_rolls:
        if r.isdigit():
            num = int(r)
            # ဥပမာ -43 သို့မဟုတ် -043 ဖြင့် ဆုံးသော roll_no များကို $regex ဖြင့် လိုက်ရှာမည်
            pattern = f"-0*{num}$"
            or_conditions.append({"roll_no": {"$regex": pattern, "$options": "i"}})
        else:
            # အကယ်၍ Admin က "Sem-II-CEIT-43" လို့ အပြည့်ရိုက်ထည့်ခဲ့ရင် Exact Match သုံးမည်
            or_conditions.append({"roll_no": r})

    if not or_conditions:
        return await update.message.reply_text("⚠️ မှန်ကန်သော Roll Number များ ထည့်ပေးပါ။")

    db = get_db()
    
    # DB တွင် Query လှမ်းပစ်ခြင်း
    query = {"$or": or_conditions}
    
    result = await db.student_info.update_many(
        query,
        {"$set": {"assigned_ec_id": ec_id}}
    )
    
    await update.message.reply_text(f"✅ <b>EC @{nickname}</b> ထံသို့ ကျောင်းသား ({result.modified_count}) ယောက်ကို အောင်မြင်စွာ Assign လုပ်ပြီးပါပြီ။", parse_mode=ParseMode.HTML)

# ================= 👤 5. EC Dashboard (/me) =================
async def ec_me(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username
    
    # ၁။ လက်ရှိ User ရဲ့ နာမည်/Nickname များကို စုဆောင်းမည် (Routing လုပ်ရန်)
    my_identifiers = [str(user_id)]
    if username:
        my_identifiers.append(username.lower())
        
    # EC_MAP ရှိခဲ့လျှင် လက်ရှိ User ရဲ့ Nickname (ဥပမာ - jojo, ben) ကို ရှာမည်
    try:
        # သင့် Bot တွင် EC_MAP သတ်မှတ်ထားသော ဖိုင်မှ import လုပ်ပါ (ဥပမာ finance_tools.py ထဲမှာရှိရင် တိုက်ရိုက်သုံးပါ)
        for nick, tg_id in EC_MAP.items():
            if user_id == tg_id:
                my_identifiers.append(nick.lower())
    except Exception:
        pass

    db = get_db()
    
    # 🕵️‍♂️ ၂။ Fund အချက်အလက်များ ယူမည် (Key ပြင်ဆင်ပြီး)
    all_funds = await db.funds.find({}).to_list(length=None)
    
    archived_fund_ids = set()
    fund_owners = {}
    fund_name_map = {}
    
    for f in all_funds:
        f_id = f["fund_id"].lower()
        fund_name_map[f_id] = f.get("fund_name", f["fund_id"])
        
        if f.get("status") == "archived":
            archived_fund_ids.add(f_id)
            
        # ⚠️ ပြင်ဆင်ချက်: Database တွင် အမှန်တကယ်သိမ်းထားသော 'managed_by' Key ကို ယူမည်
        manager = f.get("managed_by")
        if manager:
            fund_owners[f_id] = manager.lower()
            
    # (sys_config ထဲက Archive အဟောင်းတွေ ပါရင် ထည့်ပေါင်းမယ်)
    sys_config = await db.sys_config.find_one({"type": "archived_funds"})
    if sys_config:
        for f in sys_config.get("funds", []):
            archived_fund_ids.add(f.lower())

    # 🌐 ၃။ ကျောင်းသား အားလုံး ကို ဆွဲထုတ်မည်
    all_students = await db.student_info.find({}).to_list(length=None)
    
    assigned_students_count = 0
    active_fund_totals = {}
    archived_fund_totals = {}
    grand_total_collected = 0
    
    # ⚙️ ၄။ ငွေများကို ပိုင်ရှင်အစစ်ထံ လမ်းကြောင်းခွဲခြင်း (The Core Logic)
    for st in all_students:
        is_my_student = (st.get("assigned_ec_id") == user_id or st.get("tg_id") == user_id)
        
        if is_my_student:
            assigned_students_count += 1
            
        paid_funds = st.get("paid_funds", {})
        for f_id, f_data in paid_funds.items():
            amount = f_data.get("amount", 0) if isinstance(f_data, dict) else f_data
            
            if amount > 0:
                clean_f_id = f_id.lower()
                fund_manager = fund_owners.get(clean_f_id, "ec") 
                
                is_my_money = False
                
                # Routing Condition အသစ်
                if fund_manager == "ec" and is_my_student:
                    # အများကောက်သော Fund (ec) ဖြစ်ပြီး မိမိကျောင်းသား ဖြစ်နေလျှင်
                    is_my_money = True
                elif fund_manager in my_identifiers:
                    # သီးသန့်ကောက်သော Fund ဖြစ်ပြီး၊ Manager နာမည် (ဥပမာ - jojo) နှင့် မိမိ၏ Nickname/Username ကိုက်ညီလျှင်
                    is_my_money = True
                    
                if is_my_money:
                    if clean_f_id in archived_fund_ids:
                        archived_fund_totals[clean_f_id] = archived_fund_totals.get(clean_f_id, 0) + amount
                    else:
                        active_fund_totals[clean_f_id] = active_fund_totals.get(clean_f_id, 0) + amount
                        grand_total_collected += amount
            
    # 💸 ၅။ သုံးစွဲမှု (Ledger) တွက်ချက်ခြင်း
    query_name = my_identifiers[-1] if my_identifiers else "unknown"
    ec_ledger = await db.ec_ledgers.find_one({"username": {"$in": my_identifiers}}) or {}
    total_spent = ec_ledger.get("total_spent", 0)
    net_in_hand = grand_total_collected - total_spent
    
    # 💬 ၆။ Message တည်ဆောက်ခြင်း
    if assigned_students_count == 0 and grand_total_collected == 0 and total_spent == 0 and not archived_fund_totals:
        return await update.message.reply_text("⚠️ သင်သည် EC စာရင်းတွင် မရှိပါ သို့မဟုတ် ရငွေ/သုံးငွေ မှတ်တမ်း မရှိပါ။")

    msg = f"📊 <b>EC DASHBOARD - @{username if username else query_name}</b>\n"
    msg += f"━━━━━━━━━━━━━━━━━━\n"
    msg += f"👥 တာဝန်ယူထားသော ကျောင်းသား: <b>{assigned_students_count}</b> ဦး\n\n"
    
    msg += f"<b>[ 🎯 လက်ရှိ ကောက်ခံရရှိမှု (Active) ]</b>\n"
    if active_fund_totals:
        for f_id, f_amt in active_fund_totals.items():
            display_name = fund_name_map.get(f_id, f_id)
            msg += f"• {display_name}: <b>{f_amt:,} MMK</b>\n"
    else:
        msg += "<i>လက်ရှိ ကောက်ခံထားသော ငွေမရှိသေးပါ</i>\n"
        
    msg += f"📥 <b>စုစုပေါင်း ရငွေ: {grand_total_collected:,} MMK</b>\n\n"
    
    msg += f"<b>[ 💸 သုံးစွဲမှုနှင့် လက်ကျန် ]</b>\n"
    msg += f"📤 သုံးစွဲပြီးငွေ: <b>{total_spent:,} MMK</b>\n"
    msg += f"💰 <b>လက်ထဲတွင် ကျန်ငွေ: {net_in_hand:,} MMK</b>\n"
    
    if archived_fund_totals:
        msg += f"\n📦 <b>[ ဆရာမထံ အပ်နှံပြီးသော ရန်ပုံငွေများ ]</b>\n"
        for f_id, f_amt in archived_fund_totals.items():
            display_name = fund_name_map.get(f_id, f_id)
            msg += f"• {display_name}: <b>{f_amt:,} MMK</b>\n"
            
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

# ================= 📦 ဆရာမထံ ငွေအပ်နှံရန် (/archive_fund) =================
async def archive_fund_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_super_admin(update.effective_user.id): return
    
    if not context.args: return await update.message.reply_text("⚠️ <b>အသုံးပြုနည်း:</b> <code>/archive_fund &lt;FundID&gt;</code>", parse_mode=ParseMode.HTML)
    fund_id = context.args[0].lower()
    
    db = get_db()
    res = await db.funds.update_one({"fund_id": fund_id}, {"$set": {"status": "archived"}})
    
    if res.modified_count:
        await update.message.reply_text(f"✅ ရန်ပုံငွေ <code>{fund_id}</code> အား ဆရာမထံ အပ်နှံပြီးကြောင်း မှတ်တမ်းတင်လိုက်ပါပြီ။ EC များ၏ လက်ကျန်ငွေ တွက်ချက်မှုနှင့် Active Vault တွင် ပါဝင်တော့မည် မဟုတ်ပါ။", parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text("⚠️ ထို Fund ID မရှိပါ (သို့) Archive လုပ်ပြီးသား ဖြစ်နေပါသည်။")

# ================= 📈 7. ငွေစာရင်း Report ထုတ်ရန် (/export_finance) =================
async def export_finance_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_super_admin(update.effective_user.id): return
    
    await update.message.reply_text("⏳ စာရင်းရှင်းတမ်း ဖိုင်ထုတ်လုပ်နေပါသည်...")
    db = get_db()
    
    output = io.StringIO()
    writer = csv.writer(output)
    
    writer.writerow(["--- Student Fund Collection ---"])
    writer.writerow(["Roll No", "Name", "Telegram", "Fund Name", "Paid Amount"])
    
    students = await db.student_info.find({}).to_list(length=None)
    total_collected = 0
    
    for st in students:
        paid_funds = st.get("paid_funds", {})
        for fund_name, amount in paid_funds.items():
            writer.writerow([
                st.get("roll_no", ""), 
                st.get("name", ""), 
                f"@{st.get('tg_username', '')}", 
                fund_name, 
                amount
            ])
            total_collected += amount
            
    writer.writerow(["", "", "", "Total Collected:", total_collected])
    writer.writerow([]) 
    
    writer.writerow(["--- EC Expenses Report ---"])
    writer.writerow(["EC Username", "Date", "Reason", "Amount Spent"])
    
    ec_ledgers = await db.ec_ledgers.find({}).to_list(length=None)
    total_ec_spent = 0
    
    for ec in ec_ledgers:
        for exp in ec.get("expense_history", []):
            writer.writerow([
                f"@{ec.get('username', '')}",
                exp.get("date", ""),
                exp.get("reason", ""),
                exp.get("amount", 0)
            ])
            total_ec_spent += exp.get("amount", 0)
            
    writer.writerow(["", "", "Total EC Spent:", total_ec_spent])
    
    output.seek(0)
    file_name = f"Finance_Report_{datetime.now().strftime('%Y_%m_%d')}.csv"
    
    csv_file = InputFile(io.BytesIO(output.getvalue().encode('utf-8-sig')), filename=file_name)
    
    await context.bot.send_document(
        chat_id=update.effective_chat.id,
        document=csv_file,
        caption="📊 လစဉ်/Term အလိုက် ငွေစာရင်း ရှင်းတမ်း CSV ဖိုင် ရပါပြီ။"
    )

# ================= 🧨 မလိုချင်သော Fund များကို အမြစ်ဖြတ်ရန် (Secret Tool) =================
async def nuke_funds_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.auth import is_super_admin
    if not is_super_admin(update.effective_user.id): return
    
    db = get_db()
    
    # Database ထဲက paid_funds အောက်မှာရှိတဲ့ mc, extra, augest တွေကို ဖြုတ်ချမည်
    result = await db.student_info.update_many(
        {},
        {"$unset": {
            "paid_funds.mc": "", 
            "paid_funds.extra": "", 
            "paid_funds.augest": ""
        }}
    )
    
    await update.message.reply_text(f"✅ မလိုချင်သော Fund များကို ကျောင်းသား ({result.modified_count}) ယောက်၏ မှတ်တမ်းမှ အမြစ်ဖြတ် ရှင်းလင်းပြီးပါပြီ။")

# ================= 🔓 Fund အား Active အဖြစ် ပြန်ပြောင်းရန် (/unarchive_fund) =================
async def unarchive_fund_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_super_admin(update.effective_user.id): return
    
    if not context.args: 
        return await update.message.reply_text("⚠️ <b>အသုံးပြုနည်း:</b> <code>/unarchive_fund &lt;FundID&gt;</code>", parse_mode=ParseMode.HTML)
        
    fund_id = context.args[0].lower()
    db = get_db()
    
    # 1. Funds Collection ထဲတွင် status ကို "active" ပြန်ပြောင်းမည်
    res = await db.funds.update_one({"fund_id": fund_id}, {"$set": {"status": "active"}})
    
    # 2. အရင်တုန်းက sys_config ထဲမှာ သိမ်းခဲ့ဖူးလျှင်လည်း အဲ့ဒီထဲကပါ ဖြုတ်ထုတ်ပေးမည်
    await db.sys_config.update_one(
        {"type": "archived_funds"},
        {"$pull": {"funds": fund_id}}
    )
    
    if res.modified_count > 0 or res.matched_count > 0:
        await update.message.reply_text(
            f"✅ ရန်ပုံငွေ <code>{fund_id}</code> အား <b>Active</b> အဖြစ် ပြန်လည်ပြောင်းလဲလိုက်ပါပြီ။\n"
            f"ယခုအခါ `/me` Dashboard နှင့် Vault တို့တွင် ပုံမှန်အတိုင်း ပြန်လည်ပါဝင်လာပါမည်။", 
            parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text(f"⚠️ <code>{fund_id}</code> အမည်ဖြင့် ရန်ပုံငွေ ရှာမတွေ့ပါ။", parse_mode=ParseMode.HTML)