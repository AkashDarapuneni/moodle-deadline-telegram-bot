import os
import re
from contextlib import asynccontextmanager
from http import HTTPStatus

import requests
from fastapi import FastAPI, Request, Response
from sqlalchemy.orm import Session
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from database import SessionLocal, User, engine, Base
from parser import sync_moodle_calendar

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)

application = (
    Application.builder()
    .token(TELEGRAM_BOT_TOKEN)
    .updater(None)
    .build()
)


def extract_url(text: str) -> str | None:
    match = URL_PATTERN.search(text.strip())
    if not match:
        return None
    return match.group(0).rstrip(".,)")


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Welcome to the Moodle Deadline Tracker.\n\n"
        "I monitor your Moodle assignment deadlines and send timely reminders before they are due.\n\n"
        "To get started, please reply with your Moodle Calendar (.ics) link or paste the text content from the file."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    text_payload = update.message.text.strip()
    chat_id = update.effective_chat.id
    db = SessionLocal()

    await update.message.reply_text("🔄 Checking assignments...")

    try:
        # Pass payload straight to parser to decide between text parsing vs network fetching
        count = sync_moodle_calendar(db, chat_id, text_payload)
        
        if count == 0:
            await update.message.reply_text("No upcoming assignments.")
        else:
            await update.message.reply_text(
                f"✅ Sync complete! Found {count} upcoming assignments and scheduled your alerts."
            )
            
    except ValueError:
        db.rollback()
        # Triggered if data block lacks 'BEGIN:VCALENDAR' entirely
        await update.message.reply_text("Please enter a valid link or calendar text content.")
    except requests.RequestException:
        db.rollback()
        # Triggered if url fails handshake due to data center IP blacklist
        await update.message.reply_text(
            "Network connection blocked by university firewall. "
            "Please copy and paste the raw text inside your downloaded (.ics) file directly here!"
        )
    except Exception:
        db.rollback()
        await update.message.reply_text("Something went wrong. Please try again later.")
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