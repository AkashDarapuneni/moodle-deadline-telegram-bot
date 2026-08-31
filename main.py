import os
import random
import json
import asyncio
from datetime import datetime, timedelta, timezone
import requests
import icalendar
import google.generativeai as genai
from fastapi import FastAPI, Request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from telegram.request import HTTPXRequest

from database import SessionLocal, User, Deadline

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL", "")

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
ai_model = genai.GenerativeModel('gemini-1.5-flash')

app = FastAPI()

# Bulletproof Timeout Configuration for Render Network 
request_defaults = HTTPXRequest(read_timeout=30.0, connect_timeout=30.0)
application = Application.builder().token(TOKEN).updater(None).request(request_defaults).build()

# ---------------------------------------------------------
# TOLLYWOOD STEALTH ALERTS DICTIONARY
# ---------------------------------------------------------
TFI_CRAZY_ALERTS = {
    "24h": [
        "⏳ *\"Samayam ledu mithrama...\"*\n\n24 hours is less than you think. Open your laptop!\nhttps://media.tenor.com/gautamiputra-satakarni.gif",
        "⏳ *\"Nenochesa ani cheppu... 24 hours timeline start aindi ani cheppu!\"*\n\nStart writing!\nhttps://media.tenor.com/aravinda-sametha-ntr.gif",
        "⏳ *\"LMS ante flower anukunnava? FIRE-U!\"*\n\n24 hours left, thaggedhe le!\nhttps://media.tenor.com/pushpa-allu-arjun.gif",
        "⏳ *\"Boss is back... to remind you!\"*\n\nInka 24 hours undi, slow ga start chey.\nhttps://media.tenor.com/chiranjeevi-boss.gif",
        "⏳ *\"Sound vinapadutundha? 24 hours countdown start aindi!\"*\n\nGet ready.\nhttps://media.tenor.com/rangasthalam-ram-charan.gif"
    ],
    "6h": [
        "🪓 *\"Flute jinka mundu oodu... LMS server mundu kaadu!\"*\n\nStop playing games!\nhttps://media.tenor.com/balayya-legend.gif",
        "🪓 *\"Bhayapaddava? Bhayam... naaku teliyani kotha padham... kani neeku modhalavvali!\"*\n\n6 Hours left!\nhttps://media.tenor.com/ntr-temper.gif",
        "🪓 *\"Party leda pushpa?\"*\n\nAssignment rayi firstu, tarvata party!\nhttps://media.tenor.com/pushpa-party.gif"
    ],
    "2h": [
        "🚨 *\"Don't trouble the deadline! If you trouble the deadline, deadline troubles you!\"*\n\n2 hours left!\nhttps://media.tenor.com/balayya-dont-trouble.gif",
        "🚨 *\"YamaGola aypotundi ra... LMS lo submit chey 2 hours ey undi!\"*\nhttps://media.tenor.com/ntr-yamadonga.gif"
    ],
    "1h": [
        "💣 *\"Akhandaaaa!!! LMS server eppudaina padipovachu, submit it immediately!\"*\nhttps://media.tenor.com/akhanda-balayya.gif",
        "💣 *\"Arey, nenu matladedi vinapadatledaa!! Just 60 minutes left.\"*\n\nDrop everything and upload!\nhttps://media.tenor.com/temper-ntr.gif"
    ],
    "50m": [
        "💀 *\"Arey entra idhi... asalu em jarugutondi akkada! 50 mins ki submit nokkara!\"*\nhttps://media.tenor.com/brahmi-king.gif",
        "💀 *\"Last minute server crash chusava... mind block aypoddi! UPLOAD UPLOAD!\"*\nhttps://media.tenor.com/ntr-shock.gif"
    ]
}

async def analyze_task_with_ai(title: str):
    prompt = f"""
    Analyze this college assignment: "{title}".
    Classify difficulty into exactly one of these: "🟢 Chill Task", "🟡 Medium Task", "🔴 Boss Level".
    Give a 1-sentence funny/sarcastic tip mixing Telugu & English slang.
    Return ONLY valid JSON: {{"level": "...", "tip": "..."}}
    """
    try:
        res = await ai_model.generate_content_async(prompt)
        text = res.text.replace("```json", "").replace("```", "").strip()
        data = json.loads(text)
        return data.get("level", "⚪ Unknown"), data.get("tip", "Do your best!")
    except:
        return "⚪ Unknown", "Stay focused and finish it."

# ---------------------------------------------------------
# BOT COMMANDS
# ---------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎉") 
    msg = (
        "🎓 **Welcome to the Elite LMS Tracker (AI Edition)** ⚡\n\n"
        "To let my AI engine track your deadlines, I need your LMS Calendar Link. "
        "Here is the exact process to get it:\n\n"
        "1️⃣ Login to `lms.kluniversity.in`\n"
        "2️⃣ Click on **Calendar** in the side menu.\n"
        "3️⃣ Scroll down and click on **Export Calendar**.\n"
        "4️⃣ Select **All events** and **Recent and next 60 days**.\n"
        "5️⃣ Click **Get calendar URL**.\n\n"
        "🔗 *Just copy that URL and paste it directly in this chat! I will auto-detect it.*"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📺 Watch Tutorial", url="https://www.youtube.com/watch")], 
        [InlineKeyboardButton("💀 View Overdue Tasks", callback_data="check_overdue")]
    ])
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=keyboard)

async def sync(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args:
        return await update.message.reply_text("❌ **Usage:** `/sync <URL>` or simply paste the link.", parse_mode="Markdown")

    db = SessionLocal()
    user = db.query(User).filter(User.telegram_chat_id == chat_id).first()
    if not user:
        user = User(telegram_chat_id=chat_id, moodle_url=context.args[0])
        db.add(user)
    else:
        user.moodle_url = context.args[0]
    db.commit()
    db.close()
    
    await update.message.reply_text("🎊")
    await update.message.reply_text("✅ **System Synced! AI Engine is now scanning your deadlines.**", parse_mode="Markdown")

async def auto_sync_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text and ("export_execute.php" in text or "lms.kluniversity.in" in text):
        context.args = [text.strip()]
        await sync(update, context)

async def overdue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id if update.effective_chat else update.callback_query.message.chat_id
    db = SessionLocal()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    deadlines = db.query(Deadline).filter(Deadline.telegram_chat_id == chat_id, Deadline.is_completed == False, Deadline.due_date < now).order_by(Deadline.due_date.desc()).all()
    db.close()
    
    if not deadlines:
        msg = "✨ **No Overdue Tasks!** You are perfectly caught up!"
    else:
        msg = "💀 **YOUR OVERDUE TASKS (GPA DANGER ZONE):**\n\n"
        for d in deadlines:
            msg += f"❌ `{d.assignment_title}`\n⏳ *Missed on:* {d.due_date.strftime('%d %b, %I:%M %p')}\n\n"
        msg += "_Try submitting late or request your professor!_"
        
    if update.callback_query:
        await update.callback_query.message.reply_text(msg, parse_mode="Markdown")
    else:
        await update.message.reply_text(msg, parse_mode="Markdown")

async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    db = SessionLocal()
    user = db.query(User).filter(User.telegram_chat_id == chat_id).first()
    db.close()
    
    if not user:
        return await update.message.reply_text("You need to sync your calendar link first!")

    msg = (
        "🏆 **YOUR ACADEMIC PROFILE**\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🔥 **Submission Streak:** `{user.streak_count}` Tasks On-Time\n"
        f"🎖️ **Current Rank:** `{user.rank}`\n\n"
        "_Complete tasks to level up to Legend status!_"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def upcoming(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    db = SessionLocal()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    deadlines = db.query(Deadline).filter(Deadline.telegram_chat_id == chat_id, Deadline.is_completed == False, Deadline.due_date >= now).order_by(Deadline.due_date.asc()).all()
    db.close()
    
    if not deadlines:
        return await update.message.reply_text("🎈\n✨ **Clear Skies!** Zero pending deadlines.")

    msg = "📋 **Your AI Battle Plan:**\n\n"
    for d in deadlines:
        msg += f"🔹 `{d.assignment_title}`\n📊 {d.difficulty}\n⏳ *Due:* {d.due_date.strftime('%d %b, %I:%M %p')}\n\n"
    await update.message.reply_text(msg, parse_mode="Markdown")

async def features(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "💎 **PREMIUM ACADEMIC OS MODULES** 💎\n\n"
        "Here are the advanced configurations running in this engine:\n\n"
        "🌅 **1. Daily Morning Digest:** Gets a 8:00 AM summary of your battle plan.\n"
        "🧠 **2. AI Syllabus RAG:** Upload PDFs and Gemini will quiz you.\n"
        "🆘 **3. Anonymous Savior Mode:** Connects stuck students with those who finished the task.\n"
        "⚙️ **4. Alert Settings:** Customize your 24h/6h/2h Tollywood meme frequency.\n"
        "🎯 **5. Lock-In (Focus):** Pomodoro state-tracking to force deep work.\n"
        "🦇 **6. Night Owl Badges:** Secret unlockables for late-night grinds.\n"
        "🔥 **7. AI Roast Mode:** Let Gemini creatively insult your overdue tasks.\n\n"
        "_Select a setting to configure:_"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚙️ Alert Settings", callback_data="coming_soon"), InlineKeyboardButton("🌅 Morning Digest", callback_data="coming_soon")],
        [InlineKeyboardButton("🆘 Savior Mode (Beta)", callback_data="coming_soon"), InlineKeyboardButton("🔥 AI Roast", callback_data="coming_soon")]
    ])
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=keyboard)

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != str(ADMIN_CHAT_ID):
        return await update.message.reply_text("⛔ **Unauthorized. Only the developer can access this.**", parse_mode="Markdown")
    db = SessionLocal()
    stats_msg = (
        "📊 **SYSTEM ANALYTICS DASHBOARD**\n"
        f"👥 Active Nodes: `{db.query(User).count()}`\n"
        f"🎯 Active Targets: `{db.query(Deadline).filter(Deadline.is_completed == False).count()}`\n"
        f"⚡ Engine: ONLINE (Webhook + TiDB SSL + Gemini AI)"
    )
    db.close()
    await update.message.reply_text(stats_msg, parse_mode="Markdown")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat_id

    if data == "check_overdue":
        await overdue(update, context)
        return
    elif data == "coming_soon":
        await query.message.reply_text("🚀 This feature is currently in Beta!")
        return
    elif data.startswith("done_"):
        deadline_id = int(data.split("_")[1])
        db = SessionLocal()
        deadline = db.query(Deadline).filter(Deadline.id == deadline_id).first()
        user = db.query(User).filter(User.telegram_chat_id == chat_id).first()
        if deadline and user:
            deadline.is_completed = True
            user.streak_count += 1
            if user.streak_count >= 10: user.rank = "Academic Legend 👑"
            elif user.streak_count >= 5: user.rank = "Scholar 🎓"
            elif user.streak_count >= 3: user.rank = "Pro Student ⚡"
            db.commit()
            await query.edit_message_text(f"🎉 **TASK NEUTRALIZED!**\n🔥 Streak: {user.streak_count}\n🎖️ Rank: {user.rank}", parse_mode="Markdown")
        db.close()
    elif data.startswith("focus_"):
        await query.edit_message_text("🎯 **Focus Mode Locked In!** I will ping you in 25 minutes.", parse_mode="Markdown")
        async def pomodoro_timer():
            await asyncio.sleep(25 * 60)
            await context.bot.send_message(chat_id=chat_id, text="⏰ **Time's Up!** Take a 5-minute break!")
        asyncio.create_task(pomodoro_timer())

application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("sync", sync))
application.add_handler(CommandHandler("upcoming", upcoming))
application.add_handler(CommandHandler("overdue", overdue))
application.add_handler(CommandHandler("profile", profile))
application.add_handler(CommandHandler("features", features))
application.add_handler(CommandHandler("stats", stats))
application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), auto_sync_link))
application.add_handler(CallbackQueryHandler(button_handler))

# ---------------------------------------------------------
# WEBHOOK ENDPOINT FOR TELEGRAM
# ---------------------------------------------------------
@app.post("/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return {"status": "ok"}

# ---------------------------------------------------------
# REMINDER & SYNC ENGINE
# ---------------------------------------------------------
@app.get("/check-reminders")
async def check_reminders():
    db = SessionLocal()
    users = db.query(User).all()
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    for user in users:
        try:
            res = requests.get(user.moodle_url, timeout=10)
            cal = icalendar.Calendar.from_ical(res.text)
            for event in cal.walk('VEVENT'):
                summary = str(event.get('summary'))
                uid = str(event.get('uid'))
                dtend = event.get('dtend').dt
                due_date = dtend if isinstance(dtend, datetime) else datetime.combine(dtend, datetime.min.time())
                if getattr(due_date, 'tzinfo', None) is not None:
                    due_date = due_date.astimezone(timezone.utc)
                due_date = due_date.replace(tzinfo=None)

                deadline = db.query(Deadline).filter(Deadline.telegram_chat_id == user.telegram_chat_id, Deadline.assignment_id == uid).first()
                if not deadline:
                    ai_level, ai_tip = await analyze_task_with_ai(summary)
                    deadline = Deadline(
                        telegram_chat_id=user.telegram_chat_id, 
                        assignment_id=uid, 
                        assignment_title=summary, 
                        due_date=due_date, 
                        difficulty=ai_level, 
                        ai_tip=ai_tip
                    )
                    db.add(deadline)
                    db.commit()
        except Exception as e:
            print(f"Error fetching data for user {user.telegram_chat_id}: {e}")

    pending = db.query(Deadline).filter(Deadline.is_completed == False, Deadline.due_date >= now_utc).all()
    for d in pending:
        time_left = d.due_date - now_utc
        due_formatted = (d.due_date + timedelta(hours=5, minutes=30)).strftime("%d %b, %I:%M %p")
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Mark as Done", callback_data=f"done_{d.id}"), InlineKeyboardButton("🎯 Lock-In", callback_data=f"focus_{d.id}")],
            [InlineKeyboardButton("🌐 Open LMS", url="https://lms.kluniversity.in/login/index.php")]
        ])
        
        alert_key = None
        if timedelta(hours=23, minutes=45) <= time_left <= timedelta(hours=24, minutes=15) and not d.sent_24h_alert:
            alert_key, d.sent_24h_alert = "24h", True
        elif timedelta(hours=5, minutes=45) <= time_left <= timedelta(hours=6, minutes=15) and not d.sent_6h_alert:
            alert_key, d.sent_6h_alert = "6h", True
        elif timedelta(hours=1, minutes=45) <= time_left <= timedelta(hours=2, minutes=15) and not d.sent_2h_alert:
            alert_key, d.sent_2h_alert = "2h", True
        elif timedelta(minutes=55) <= time_left <= timedelta(hours=1, minutes=5) and not d.sent_1h_alert:
            alert_key, d.sent_1h_alert = "1h", True
        elif timedelta(minutes=45) <= time_left <= timedelta(minutes=52) and not d.sent_50m_alert:
            alert_key, d.sent_50m_alert = "50m", True

        if alert_key:
            quote = random.choice(TFI_CRAZY_ALERTS[alert_key])
            msg = f"{quote}\n\n📌 **Task:** `{d.assignment_title}`\n⏳ **Due:** {due_formatted} IST\n📊 **AI Threat Level:** {d.difficulty}\n🤖 **Tip:** _{d.ai_tip}_"
            try:
                await application.bot.send_message(chat_id=d.telegram_chat_id, text=msg, parse_mode="Markdown", reply_markup=keyboard)
                db.commit()
            except:
                pass
    db.close()
    return {"status": "success"}

@app.on_event("startup")
async def startup_event():
    await application.initialize()
    await application.start()
    
    # Safe Webhook registration with error trapping
    if RENDER_URL:
        webhook_url = f"{RENDER_URL}/webhook"
        try:
            await application.bot.set_webhook(webhook_url)
            print(f"Webhook set successfully to: {webhook_url}")
        except Exception as e:
            print(f"Webhook setup warning: {e}")

@app.on_event("shutdown")
async def shutdown_event():
    await application.stop()
    await application.shutdown()
