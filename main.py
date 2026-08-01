# Version 1.0.6 - Smart Assistant & Robust Sync Edition
import os
import re
from contextlib import asynccontextmanager
from http import HTTPStatus
from datetime import datetime, timezone

import requests
from fastapi import FastAPI, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
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
        "1. Sync deadlines: Paste your Moodle link or raw calendar text starting with `BEGIN:VCALENDAR` once.\n"
        "2. Ask me anything: Talk naturally about your upcoming milestones, due dates, or check for overdue assignments anytime!"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    text_payload = update.message.text.strip()
    chat_id = update.effective_chat.id
    db = SessionLocal()

    # SCENARIO A: User is updating/pasting a calendar file data dump or link
    if "BEGIN:VCALENDAR" in text_payload or text_payload.startswith(("http://", "https://")):
        await update.message.reply_text("🔄 Syncing your calendar milestones...")
        try:
            is_url = text_payload.startswith(("http://", "https://"))
            
            user = db.query(User).filter(User.telegram_chat_id == chat_id).first()
            if not user:
                user = User(telegram_chat_id=chat_id)
                db.add(user)
            
            if is_url:
                user.calendar_link = text_payload  
                
            count = sync_moodle_calendar(db, chat_id, text_payload)
            user.last_sync_success = True
            db.commit()

            if count == 0:
                await update.message.reply_text("📭 No assignments found in this calendar link or file. Please check your Moodle calendar settings.")
            else:
                await update.message.reply_text(f"✅ Sync complete! Tracked {count} upcoming milestones successfully.")
                
        except ValueError:
            db.rollback()
            await update.message.reply_text("❌ The provided link or calendar text is not valid. Please check and try again.")
        except requests.RequestException:
            db.rollback()
            await update.message.reply_text(
                "❌ Network connection failed or link has expired!\n"
                "Please verify your Moodle authentication token or copy-paste the raw text content inside your downloaded (.ics) file directly here."
            )
        except Exception:
            db.rollback()
            await update.message.reply_text("❌ The provided link or calendar text is not valid. Please check and try again.")
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
        has_synced_before = user_record is not None
        
        # Fetch ALL stored deadlines (including past ones for overdue query support)
        stmt = select(Deadline).where(Deadline.telegram_chat_id == chat_id).order_by(Deadline.due_date)
        deadlines = db.scalars(stmt).all()
        
        current_time_str = datetime.now(timezone.utc).strftime("%A, %B %d, %Y at %I:%M %p UTC")
        
        context_lines = []
        for d in deadlines:
            context_lines.append(f"- Subject/Task: {d.assignment_title} | Absolute Deadline: {d.due_date.strftime('%Y-%m-%d %H:%M UTC')}")
        
        deadline_context = "\n".join(context_lines) if context_lines else "No deadlines recorded."

        system_instruction = (
            "You are an empathetic, sharp academic assistant for university students.\n\n"
            f"Current Timestamp context: {current_time_str}\n"
            f"User Profile Synced Status: {'YES' if has_synced_before else 'NO'}\n"
            f"All Tracked Deadlines (Upcoming & Overdue):\n{deadline_context}\n\n"
            "Guidelines:\n"
            "- If the user says 'hi' or greets you, reply warmly: 'Hello! How may I help you? You can ask me about your due assignments with their times, check specific dates, or ask for overdue assignments.'\n"
            "- If 'User Profile Synced Status' is NO, gently instruct them to paste their calendar link or text to get started.\n"
            "- If they ask for due assignments, list them clearly with their names and absolute times.\n"
            "- If they ask for assignments on a specific date, filter and display only the assignments due on that exact calendar date.\n"
            "- If they ask for overdue assignments, list the tasks that have passed their deadline along with how long ago they became overdue.\n"
            "- Keep responses concise, direct, and well-formatted using Markdown."
        )

        response = current_ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=text_payload,
            config={'system_instruction': system_instruction}
        )
        
        await update.message.reply_text(response.text, parse_mode="Markdown")

    except Exception:
        await update.message.reply_text("I couldn't process that query right now. Please try again in a moment.")
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
