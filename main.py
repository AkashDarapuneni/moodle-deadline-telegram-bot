import os
import random
import json
import asyncio
from datetime import datetime, timedelta, timezone
import requests
import icalendar
import google.generativeai as genai
from fastapi import FastAPI
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean
from sqlalchemy.orm import sessionmaker, declarative_base

# ---------------------------------------------------------
# 1. CONFIGURATION, AI & DATABASE SETUP
# ---------------------------------------------------------
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./lms_bot.db")

# Gemini Setup
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
ai_model = genai.GenerativeModel('gemini-2.5-flash')

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Added connect_args={"ssl": {}} here!
engine = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args={"ssl": {}})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(String, unique=True, index=True)
    moodle_url = Column(String)
    streak_count = Column(Integer, default=0) # Gamification Streak
    rank = Column(String, default="Rookie 🔰")

class Deadline(Base):
    __tablename__ = "deadlines"
    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(String, index=True)
    assignment_id = Column(String, index=True)
    assignment_title = Column(String)
    due_date = Column(DateTime)
    is_completed = Column(Boolean, default=False)
    
    # AI Feature Columns
    difficulty = Column(String, default="⚪ Unknown")
    ai_tip = Column(String, default="Just get it done!")

    # Alert Flags
    sent_24h_alert = Column(Boolean, default=False)
    sent_6h_alert = Column(Boolean, default=False)
    sent_2h_alert = Column(Boolean, default=False)
    sent_1h_alert = Column(Boolean, default=False)
    sent_50m_alert = Column(Boolean, default=False)

Base.metadata.create_all(bind=engine)
app = FastAPI()
application = Application.builder().token(TOKEN).build()

# ---------------------------------------------------------
# 2. TOLLYWOOD STEALTH ALERTS DICTIONARY
# ---------------------------------------------------------
TFI_CRAZY_ALERTS = {
    "24h": [
        "⏳ *\"Samayam ledu mithrama...\"*\n\n24 hours is less than you think. Open your laptop!\nhttps://media.tenor.com/gautamiputra-satakarni.gif",
        "⏳ *\"LMS ante flower anukunnava? FIRE-U!\"*\n\n24 hours left, thaggedhe le!\nhttps://media.tenor.com/pushpa-allu-arjun.gif"
    ],
    "6h": [
        "🪓 *\"Flute jinka mundu oodu... LMS server mundu kaadu!\"*\n\nStop playing games!\nhttps://media.tenor.com/balayya-legend.gif",
        "🪓 *\"Little hearts tintava ra thuppasi vedhava? Velli assignment rayi!\"*\nhttps://media.tenor.com/ms-narayana.gif"
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

# ---------------------------------------------------------
# 3. GEMINI AI LOGIC (Predictive Analytics)
# ---------------------------------------------------------
async def analyze_task_with_ai(title: str):
    prompt = f"""
    Analyze this college assignment: "{title}".
    Classify difficulty into exactly one of these: "🟢 Chill Task", "🟡 Medium Task", "🔴 Boss Level".
    Give a 1-sentence funny/sarcastic tip mixing Telugu & English slang (e.g., 'Mind block aipoddi, start early!').
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
# 4. BOT COMMANDS (Start, Sync, Upcoming, Profile, Stats)
# ---------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎉")
    msg = (
        "🎓 **Welcome to the Elite LMS Tracker (AI Edition)** ⚡\n\n"
        "I am your personal AI engine. I don't just remind you; I analyze task difficulty, track your streaks, and keep you focused.\n\n"
        "🔗 *Send your Moodle iCal URL to activate:* `/sync <URL>`"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def sync(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if not context.args:
        await update.message.reply_text("❌ **Usage:** `/sync <URL>`", parse_mode="Markdown")
        return

    db = SessionLocal()
    user = db.query(User).filter(User.chat_id == chat_id).first()
    if not user:
        user = User(chat_id=chat_id, moodle_url=context.args[0])
        db.add(user)
    else:
        user.moodle_url = context.args[0]
    db.commit()
    db.close()
    
    await update.message.reply_text("🎊")
    await update.message.reply_text("✅ **System Synced! AI Engine is now scanning your deadlines.**", parse_mode="Markdown")

async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    db = SessionLocal()
    user = db.query(User).filter(User.chat_id == chat_id).first()
    db.close()
    
    if not user:
        await update.message.reply_text("You need to `/sync` first!")
        return

    msg = (
        "🏆 **YOUR ACADEMIC PROFILE**\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🔥 **Submission Streak:** `{user.streak_count}` Tasks On-Time\n"
        f"🎖️ **Current Rank:** `{user.rank}`\n\n"
        "_Complete tasks to level up to Legend status!_"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def upcoming(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    db = SessionLocal()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    deadlines = db.query(Deadline).filter(Deadline.chat_id == chat_id, Deadline.is_completed == False, Deadline.due_date >= now).order_by(Deadline.due_date.asc()).all()
    db.close()
    
    if not deadlines:
        await update.message.reply_text("🎈\n✨ **Clear Skies!** Zero pending deadlines.")
        return

    msg = "📋 **Your AI Battle Plan:**\n\n"
    for d in deadlines:
        msg += f"🔹 `{d.assignment_title}`\n📊 {d.difficulty}\n⏳ *Due:* {d.due_date.strftime('%d %b, %I:%M %p')}\n\n"
    await update.message.reply_text(msg, parse_mode="Markdown")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != str(ADMIN_CHAT_ID):
        return await update.message.reply_text("⛔ **Unauthorized.**", parse_mode="Markdown")
    db = SessionLocal()
    stats_msg = (
        "📊 **SYSTEM ANALYTICS DASHBOARD**\n"
        f"👥 Active Nodes: `{db.query(User).count()}`\n"
        f"🎯 Active Targets: `{db.query(Deadline).filter(Deadline.is_completed == False).count()}`\n"
        f"⚡ Engine: ONLINE (TiDB + Gemini AI)"
    )
    db.close()
    await update.message.reply_text(stats_msg, parse_mode="Markdown")

# ---------------------------------------------------------
# 5. BUTTON CLICKS (Mark Done & Pomodoro Focus)
# ---------------------------------------------------------
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = str(query.message.chat_id)

    if data.startswith("done_"):
        deadline_id = int(data.split("_")[1])
        db = SessionLocal()
        deadline = db.query(Deadline).filter(Deadline.id == deadline_id).first()
        user = db.query(User).filter(User.chat_id == chat_id).first()
        
        if deadline and user:
            deadline.is_completed = True
            user.streak_count += 1
            # Update Gamification Rank
            if user.streak_count >= 10: user.rank = "Academic Legend 👑"
            elif user.streak_count >= 5: user.rank = "Scholar 🎓"
            elif user.streak_count >= 3: user.rank = "Pro Student ⚡"
            
            db.commit()
            
            await query.edit_message_text(f"🎉 **TASK NEUTRALIZED!**\n🔥 Streak: {user.streak_count}\n🎖️ Rank: {user.rank}", parse_mode="Markdown")
            await context.bot.send_message(chat_id=chat_id, text="🎉")
        db.close()

    elif data.startswith("focus_"):
        await query.edit_message_text("🎯 **Focus Mode Locked In!**\n\nPut your phone away. I will ping you in 25 minutes to take a break. Get to work!", parse_mode="Markdown")
        # Async delay for 25 minutes without blocking the bot
        async def pomodoro_timer():
            await asyncio.sleep(25 * 60)
            await context.bot.send_message(chat_id=chat_id, text="⏰ **Time's Up!**\nGreat focus session. Take a 5-minute break and check /upcoming if you need to go again!")
        asyncio.create_task(pomodoro_timer())

application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("sync", sync))
application.add_handler(CommandHandler("upcoming", upcoming))
application.add_handler(CommandHandler("profile", profile))
application.add_handler(CommandHandler("stats", stats))
application.add_handler(CallbackQueryHandler(button_handler))

# ---------------------------------------------------------
# 6. CRON JOB: SCAN & NOTIFY ENGINE
# ---------------------------------------------------------
@app.get("/check-reminders")
async def check_reminders():
    db = SessionLocal()
    users = db.query(User).all()
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    for user in users:
        try:
            res = requests.get(user.moodle_url, timeout=10)
            cal = icalendar.Calendar.from_ical(res.text)

            for event in cal.walk('VEVENT'):
                summary = str(event.get('summary'))
                uid = str(event.get('uid'))
                dtend = event.get('dtend').dt
                due_date = dtend.astimezone(timezone.utc).replace(tzinfo=None) if isinstance(dtend, datetime) else datetime.combine(dtend, datetime.min.time())

                deadline = db.query(Deadline).filter(Deadline.chat_id == user.chat_id, Deadline.assignment_id == uid).first()

                # IF NEW ASSIGNMENT -> CALL GEMINI AI IN BACKGROUND
                if not deadline:
                    ai_level, ai_tip = await analyze_task_with_ai(summary)
                    deadline = Deadline(chat_id=user.chat_id, assignment_id=uid, assignment_title=summary, due_date=due_date, difficulty=ai_level, ai_tip=ai_tip)
                    db.add(deadline)
                    db.commit()
        except:
            pass

    pending = db.query(Deadline).filter(Deadline.is_completed == False, Deadline.due_date >= now).all()

    for d in pending:
        time_left = d.due_date - now
        due_formatted = (d.due_date + timedelta(hours=5, minutes=30)).strftime("%d %b, %I:%M %p")
        
        # Interactive Buttons
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Mark as Done", callback_data=f"done_{d.id}"), InlineKeyboardButton("🎯 Lock-In (Focus)", callback_data=f"focus_{d.id}")],
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
            # Premium Message Format with AI Data
            msg = (
                f"{quote}\n\n"
                f"📌 **Task:** `{d.assignment_title}`\n"
                f"⏳ **Due:** {due_formatted} IST\n"
                f"📊 **AI Threat Level:** {d.difficulty}\n"
                f"🤖 **Tip:** _{d.ai_tip}_"
            )
            try:
                await application.bot.send_message(chat_id=d.chat_id, text=msg, parse_mode="Markdown", reply_markup=keyboard)
                db.commit()
            except:
                pass

    db.close()
    return {"status": "success", "processed_at": now.isoformat()}
