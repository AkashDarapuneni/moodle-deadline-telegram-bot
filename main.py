# Version 1.0.8 - Debug Error Exposure Edition
import os
from contextlib import asynccontextmanager
from http import HTTPStatus
from datetime import datetime, timezone

import requests
from fastapi import FastAPI, Request, Response
from sqlalchemy import select
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from google import genai

from database import SessionLocal, User, engine, Base, Deadline
from parser import sync_moodle_calendar

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

application = (
    Application.builder()
    .token(TELEGRAM_BOT_TOKEN)
    .updater(None)
    .build()
)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Welcome to your AI Moodle Tracker!\n\n"
        "✨ **What I can do:**\n"
        "1. Sync deadlines: Paste your Moodle link or raw calendar text *once*. I will save it permanently.\n"
        "2. Ask me anything: Talk naturally about your due assignments, specific dates, or overdue tasks anytime!"
    )


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
                user = User(telegram_chat_id=chat_id, moodle_url="")
                db.add(user)
            
            if is_url:
                user.calendar_link = text_payload  # Persist the link permanently
                
            count = sync_moodle_calendar(db, chat_id, text_payload)
            db.commit()

            if count == 0:
                await update.message.reply_text("📭 No assignments found in this calendar link or file. Please check your Moodle calendar settings.")
            else:
                await update.message.reply_text(f"✅ Sync complete! Tracked {count} upcoming milestones successfully. I have saved your link securely.")
                
        except ValueError:
            db.rollback()
            await update.message.reply_text("❌ The provided link or calendar text is not valid. Please check and try again.")
        except requests.RequestException:
            db.rollback()
            await update.message.reply_text("❌ Link was expired. Please generate a new Moodle calendar export URL and send it here.")
        except Exception:
            db.rollback()
            await update.message.reply_text("❌ Link was expired or invalid. Please provide an active calendar link.")
        finally:
            db.close()
        return

    # SCENARIO B: Conversational Assistant Query
    local_api_key = os.getenv("GEMINI_API_KEY")
    if not local_api_key:
        await update.message.reply_text("AI features are currently unavailable. Ensure GEMINI_API_KEY is configured on Render.")
        db.close()
        return

    try:
        current_ai_client = genai.Client(api_key=local_api_key)
        
        user_record = db.query(User).filter(User.telegram_chat_id == chat_id).first()
        
        # If user has a saved link, perform a live check in the background to ensure it hasn't expired
        if user_record and user_record.calendar_link:
            try:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Accept": "text/calendar,text/html,*/*"
                }
                res = requests.get(user_record.calendar_link, headers=headers, timeout=10)
                if res.status_code >= 400 or "BEGIN:VCALENDAR" not in res.text:
                    await update.message.reply_text("❌ Link was expired. Please send your updated Moodle calendar link.")
                    db.close()
                    return
            except Exception:
                await update.message.reply_text("❌ Link was expired. Please send your updated Moodle calendar link.")
                db.close()
                return

        has_synced_before = user_record is not None and user_record.calendar_link is not None
        
        stmt = select(Deadline).where(Deadline.telegram_chat_id == chat_id).order_by(Deadline.due_date)
        deadlines = db.scalars(stmt).all()
        
        current_time_str = datetime.now(timezone.utc).strftime("%A, %B %d, %Y at %I:%M %p UTC")
        
        context_lines = []
        for d in deadlines:
            context_lines.append(f"- Subject/Task: {d.assignment_title} | Absolute Deadline: {d.due_date.strftime('%Y-%m-%d %H:%M UTC')}")
        
        deadline_context = "\n".join(context_lines) if context_lines else "No deadlines recorded."

        system_prompt = (
            "You are an empathetic, sharp academic assistant for university students.\n\n"
            f"Current Timestamp context: {current_time_str}\n"
            f"User Profile Synced Status: {'YES' if has_synced_before else 'NO'}\n"
            f"All Tracked Deadlines (Upcoming & Overdue):\n{deadline_context}\n\n"
            "Guidelines:\n"
            "- If the user says 'hi' or greets you, reply warmly: 'Hello! How may I help you? You can ask me about your due assignments with their times, check specific dates, or ask for overdue assignments.'\n"
            "- CRITICAL: Since the user's link is permanently stored, NEVER ask them to paste their link or calendar again if 'User Profile Synced Status' is YES.\n"
            "- If they ask for due assignments, list them clearly with names and absolute times.\n"
            "- If they ask for assignments on a specific date, filter and display only assignments due on that exact date.\n"
            "- If they ask for overdue assignments, list past-due tasks and how long ago they became overdue.\n"
            "- Keep responses concise, direct, and well-formatted using Markdown."
        )

        response = current_ai_client.models.generate_content(
            model='gemini-2.0-flash',
            contents=f"{system_prompt}\n\nUser Message: {text_payload}"
        )
        
        await update.message.reply_text(response.text, parse_mode="Markdown")

    except Exception as e:
        print(f"Gemini API Error: {e}")
        # Exposing the exact error string in Telegram for rapid debugging
        await update.message.reply_text(f"⚠️ Error: {e}")
    finally:
        db.close()


application.add_handler(CommandHandler("start", start_command))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    if WEBHOOK_URL:
        target_url = WEBHOOK_URL if WEBHOOK_URL.endswith("/webhook") else f"{WEBHOOK_URL.rstrip('/')}/webhook"
        await application.bot.set_webhook(url=target_url)
    async with application:
        await application.start()
        yield
        await application.stop()


app = FastAPI(lifespan=lifespan)


@app.post("/webhook")
async def webhook(request: Request) -> Response:
    update = Update.de_json(await request.json(), application.bot)
    await application.process_update(update)
    return Response(status_code=HTTPStatus.OK)
