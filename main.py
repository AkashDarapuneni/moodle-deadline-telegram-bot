# Version 1.0.4 - Production AI Integration Release
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
        "1. Sync deadlines: Just paste your raw Moodle calendar text starting with `BEGIN:VCALENDAR`.\n"
        "2. Ask me anything: Ask about 'this week's assignments', 'particular subject status', or 'overdue items' naturally!"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    text_payload = update.message.text.strip()
    chat_id = update.effective_chat.id
    db = SessionLocal()

    # SCENARIO A: User is uploading/pasting a calendar file data dump
    if "BEGIN:VCALENDAR" in text_payload or text_payload.startswith(("http://", "https://")):
        await update.message.reply_text("🔄 Syncing your calendar milestones...")
        try:
            count = sync_moodle_calendar(db, chat_id, text_payload)
            if count == 0:
                await update.message.reply_text("Your calendar structural file is valid but contains 0 upcoming events.")
            else:
                await update.message.reply_text(f"✅ Sync complete! Tracked {count} upcoming milestones successfully.")
        except ValueError:
            db.rollback()
            await update.message.reply_text("Please enter a valid link or structural calendar text content.")
        except requests.RequestException:
            db.rollback()
            await update.message.reply_text("Network connection blocked by university firewall. Please copy and paste raw text dump directly here!")
        except Exception:
            db.rollback()
            await update.message.reply_text("Something went wrong with parsing. Please try again.")
        finally:
            db.close()
        return

    # SCENARIO B: Conversational Assistant Query (Handling student questions using AI)
    local_api_key = os.getenv("GEMINI_API_KEY")
    if not local_api_key:
        await update.message.reply_text("AI features are currently unavailable. Ensure GEMINI_API_KEY is configured on Render.")
        db.close()
        return

    try:
        # Initialize client dynamically inside request boundary scope to catch hot-reloads
        current_ai_client = genai.Client(api_key=local_api_key)
        
        # 1. Fetch user's stored tracking deadlines from database to give AI context
        stmt = select(Deadline).where(Deadline.telegram_chat_id == chat_id).order_by(Deadline.due_date)
        deadlines = db.scalars(stmt).all()
        
        current_time_str = datetime.now(timezone.utc).strftime("%A, %B %d, %Y at %I:%M %p UTC")
        
        # 2. Build the structural background context block
        context_lines = []
        for d in deadlines:
            context_lines.append(f"- Subject/Task: {d.assignment_title} | Absolute Deadline: {d.due_date.strftime('%Y-%m-%d %H:%M UTC')}")
        
        deadline_context = "\n".join(context_lines) if context_lines else "No upcoming deadlines tracked yet."

        # 3. Formulate structural context guidelines for the LLM core processor
        system_instruction = (
            "You are an empathetic, sharp academic assistant for university students. "
            "You answer questions regarding homework, quizzes, ALMs (Active Learning Modules), and home assignments based STRICTLY on the student data provided below.\n\n"
            f"Current Timestamp context: {current_time_str}\n"
            f"Student's Tracked Deadlines:\n{deadline_context}\n\n"
            "Guidelines:\n"
            "- Be concise, direct, and conversational.\n"
            "- If a student asks about 'this week', evaluate deadlines relative to the current timestamp.\n"
            "- If a deadline's date has passed compared to the current timestamp, flag it clearly as OVERDUE.\n"
            "- Answer questions about specific subjects by parsing titles (e.g., matching 'DBMS' or 'Java').\n"
            "- If no data matches or list is empty, remind them gently to paste their calendar text dump first."
        )

        # 4. Request generation using the active client instance
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