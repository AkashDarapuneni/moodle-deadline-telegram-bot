# Version 1.6.0 - Smart Link Helper & Adaptive AI Edition
import os
from contextlib import asynccontextmanager
from http import HTTPStatus
from datetime import datetime, timezone, timedelta

import requests
from fastapi import FastAPI, Request, Response
from sqlalchemy import select, text
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from google import genai

from database import SessionLocal, User, engine, Base, Deadline
from parser import sync_moodle_calendar

# Define Indian Standard Time (IST = UTC + 5:30)
IST = timezone(timedelta(hours=5, minutes=30))

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

application = (
    Application.builder()
    .token(TELEGRAM_BOT_TOKEN)
    .updater(None)
    .build()
)

# Shared helper message for unsynced users
UNSYNCED_MESSAGE = (
    "⚠️ **Please provide your Moodle calendar link or calendar text!**\n\n"
    "To start tracking your assignments and receiving automated reminders, I need your Moodle export URL.\n\n"
    "📌 **How to get your link:**\n"
    "1️⃣ Log in at [lms.kluniversity.in](https://lms.kluniversity.in)\n"
    "2️⃣ Click **Calendar** on the left menu\n"
    "3️⃣ Scroll down and click **Export calendar**\n"
    "4️⃣ Select **All events** & **Recent and next 60 days**, then click **Get calendar URL**\n"
    "5️⃣ Copy and paste the link directly into this chat!\n\n"
    "🎥 **Video Tutorial:** [Watch Step-by-Step Video](https://youtu.be/_mbkqrZ6ZHQ)"
)

# Shared helper message for synced users
SYNCED_INFO_MESSAGE = (
    "✅ **Your Moodle Calendar is Synced & Active!**\n\n"
    "🤖 **What I do for you:**\n"
    "• **Automatic Reminders:** I will automatically send you Telegram alerts **24h, 6h, 2h, 1h, and 50m** before any task is due.\n"
    "• **Live Updates:** I continuously sync with your Moodle calendar to catch newly posted assignments.\n"
    "• **AI Assistant:** You can ask me anything about your academic schedule in plain English.\n\n"
    "💬 **How to chat with me:**\n"
    "Simply type a message in this chat! Here are a few examples of what you can ask:\n"
    "👉 *\"What assignments are due this week?\"*\n"
    "👉 *\"Do I have anything due tomorrow?\"*\n"
    "👉 *\"List all my pending quizzes.\"*\n"
    "👉 *\"When is my next project deadline?\"*"
)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.telegram_chat_id == chat_id).first()
        if user and user.calendar_link:
            await update.message.reply_text(SYNCED_INFO_MESSAGE, parse_mode="Markdown")
        else:
            await update.message.reply_text(UNSYNCED_MESSAGE, parse_mode="Markdown", disable_web_page_preview=False)
    finally:
        db.close()


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    text_payload = update.message.text.strip()
    chat_id = update.effective_chat.id
    db = SessionLocal()

    # SCENARIO A: User is providing or updating their calendar link/text
    if "BEGIN:VCALENDAR" in text_payload or text_payload.startswith(("http://", "https://")):
        await update.message.reply_text("🔄 Syncing your calendar milestones...")
        try:
            is_url = text_payload.startswith(("http://", "https://"))
            
            user = db.query(User).filter(User.telegram_chat_id == chat_id).first()
            if not user:
                user = User(
                    telegram_chat_id=chat_id, 
                    moodle_url="", 
                    calendar_link=text_payload if is_url else None
                )
                db.add(user)
            else:
                if is_url:
                    user.calendar_link = text_payload
            
            db.commit()
            count = sync_moodle_calendar(db, chat_id, text_payload)

            if count == 0:
                await update.message.reply_text("📭 No assignments found in this calendar link. Please verify your Moodle export options.")
            else:
                await update.message.reply_text(
                    f"✅ Sync complete! Tracked **{count}** upcoming milestones successfully.\n\n" + SYNCED_INFO_MESSAGE,
                    parse_mode="Markdown"
                )
                
        except ValueError as val_err:
            db.rollback()
            await update.message.reply_text(f"❌ Sync Failed: {val_err}")
        except requests.RequestException:
            db.rollback()
            await update.message.reply_text("❌ Link was expired or unreachable. Please generate a new Moodle calendar export URL.")
        except Exception as e:
            db.rollback()
            await update.message.reply_text(f"❌ Error during sync: {e}")
        finally:
            db.close()
        return

    # Check if user has synced before processing conversational messages
    user_record = db.query(User).filter(User.telegram_chat_id == chat_id).first()
    has_synced_before = user_record is not None and bool(user_record.calendar_link)

    # SCENARIO B: Unsynced user trying to chat
    if not has_synced_before:
        await update.message.reply_text(UNSYNCED_MESSAGE, parse_mode="Markdown", disable_web_page_preview=False)
        db.close()
        return

    # SCENARIO C: Synced Conversational Assistant Query
    local_api_key = os.getenv("GEMINI_API_KEY")
    if not local_api_key:
        await update.message.reply_text("AI features are currently unavailable. Ensure GEMINI_API_KEY is configured on Render.")
        db.close()
        return

    try:
        current_ai_client = genai.Client(api_key=local_api_key)
        
        # Live background sync check
        if user_record and user_record.calendar_link:
            try:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Accept": "text/calendar,text/html,*/*"
                }
                res = requests.get(user_record.calendar_link, headers=headers, timeout=10)
                if res.status_code < 400 and "BEGIN:VCALENDAR" in res.text:
                    sync_moodle_calendar(db, chat_id, res.text)
            except Exception as live_err:
                print(f"Live Sync Warning: {live_err}")

        stmt = select(Deadline).where(Deadline.telegram_chat_id == chat_id).order_by(Deadline.due_date)
        deadlines = db.scalars(stmt).all()
        
        now_ist = datetime.now(timezone.utc).astimezone(IST)
        current_time_str = now_ist.strftime("%A, %B %d, %Y at %I:%M %p IST")
        
        context_lines = []
        for d in deadlines:
            due_utc = d.due_date
            if due_utc.tzinfo is None:
                due_utc = due_utc.replace(tzinfo=timezone.utc)
            due_ist = due_utc.astimezone(IST)
            context_lines.append(
                f"- Subject/Task: {d.assignment_title} | Absolute Deadline: {due_ist.strftime('%d %b %Y, %I:%M %p IST')}"
            )
        
        deadline_context = "\n".join(context_lines) if context_lines else "No deadlines recorded."

        # UPDATED SYSTEM PROMPT WITH EXACT INSTRUCTIONS
        system_prompt = (
            "You are an empathetic, sharp academic assistant for university students at KL University.\n\n"
            f"Current Timestamp context: {current_time_str}\n"
            f"All Tracked Deadlines (Upcoming & Overdue):\n{deadline_context}\n\n"
            "Guidelines:\n"
            "- If the user says 'hi' or greets you, reply warmly.\n"
            "- IF THE USER ASKS HOW TO GET, EXPORT, OR UPDATE THEIR MOODLE/LMS LINK, give them these exact steps:\n"
            "  1. Log in to lms.kluniversity.in\n"
            "  2. Click 'Calendar' on the left menu.\n"
            "  3. Scroll down and click 'Export calendar'.\n"
            "  4. Select 'All events' and 'Recent and next 60 days', then click 'Get calendar URL'.\n"
            "  5. Copy and paste the link directly into this chat.\n"
            "  Provide the YouTube tutorial video link too: https://youtu.be/_mbkqrZ6ZHQ\n"
            "- Compare the current IST timestamp with task deadlines to answer time-relative questions accurately.\n"
            "- List due assignments clearly with names and absolute times in Indian Standard Time (IST).\n"
            "- Keep responses concise, direct, and well-formatted using Markdown."
        )

        response = current_ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=f"{system_prompt}\n\nUser Message: {text_payload}"
        )
        
        await update.message.reply_text(response.text, parse_mode="Markdown")

    except Exception as e:
        print(f"Gemini API Error: {e}")
        await update.message.reply_text(f"⚠️ Error: {e}")
    finally:
        db.close()


application.add_handler(CommandHandler("start", start_command))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    
    with engine.connect() as conn:
        columns_to_patch = [
            ("users", "calendar_link", "VARCHAR(512)"),
            ("deadlines", "sent_2h_alert", "BOOLEAN DEFAULT FALSE"),
            ("deadlines", "sent_50m_alert", "BOOLEAN DEFAULT FALSE"),
        ]
        for table, col, col_type in columns_to_patch:
            try:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {col_type};"))
                conn.commit()
            except Exception:
                pass
            
    if WEBHOOK_URL:
        target_url = WEBHOOK_URL if WEBHOOK_URL.endswith("/webhook") else f"{WEBHOOK_URL.rstrip('/')}/webhook"
        await application.bot.set_webhook(url=target_url)
    async with application:
        await application.start()
        yield
        await application.stop()


app = FastAPI(lifespan=lifespan)


@app.get("/check-reminders")
async def check_reminders():
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        stmt = select(Deadline)
        deadlines = db.scalars(stmt).all()
        
        for d in deadlines:
            due = d.due_date
            if due.tzinfo is None:
                due = due.replace(tzinfo=timezone.utc)
                
            time_left = due - now
            chat_id = d.telegram_chat_id

            due_ist = due.astimezone(IST)
            due_formatted = due_ist.strftime('%d %b %Y, %I:%M %p IST')

            # 1. 24-HOUR ALERT
            if timedelta(hours=23, minutes=45) <= time_left <= timedelta(hours=24, minutes=15) and not getattr(d, 'sent_24h_alert', False):
                msg = f"⏰ **24-Hour Reminder!**\n\nTask: **{d.assignment_title}**\nDue: {due_formatted}"
                await application.bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")
                d.sent_24h_alert = True

            # 2. 6-HOUR ALERT
            elif timedelta(hours=5, minutes=45) <= time_left <= timedelta(hours=6, minutes=15) and not getattr(d, 'sent_6h_alert', False):
                msg = f"⚠️ **6-Hour Warning!**\n\nTask: **{d.assignment_title}**\nDue: {due_formatted}"
                await application.bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")
                d.sent_6h_alert = True

            # 3. 2-HOUR ALERT
            elif timedelta(hours=1, minutes=45) <= time_left <= timedelta(hours=2, minutes=15) and not getattr(d, 'sent_2h_alert', False):
                msg = f"⏳ **2-Hour Alert!**\n\nTask: **{d.assignment_title}**\nDue: {due_formatted}"
                await application.bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")
                d.sent_2h_alert = True

            # 4. 1-HOUR ALERT
            elif timedelta(minutes=55) <= time_left <= timedelta(hours=1, minutes=5) and not getattr(d, 'sent_1h_alert', False):
                msg = f"🚨 **1-Hour Urgent Alert!**\n\nTask: **{d.assignment_title}**\nDue: {due_formatted}"
                await application.bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")
                d.sent_1h_alert = True

            # 5. 50-MINUTE ALERT
            elif timedelta(minutes=45) <= time_left <= timedelta(minutes=52) and not getattr(d, 'sent_50m_alert', False):
                msg = f"🔥 **50 Minutes Remaining!**\n\nTask: **{d.assignment_title}**\nDue: {due_formatted}"
                await application.bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")
                d.sent_50m_alert = True

        db.commit()
        return {"status": "success", "checked_at": now.astimezone(IST).isoformat()}
    except Exception as e:
        db.rollback()
        return {"status": "error", "detail": str(e)}
    finally:
        db.close()


@app.post("/webhook")
async def webhook(request: Request) -> Response:
    update = Update.de_json(await request.json(), application.bot)
    await application.process_update(update)
    return Response(status_code=HTTPStatus.OK)
