import os
import re
from contextlib import asynccontextmanager
from http import HTTPStatus

import requests
from fastapi import FastAPI, Request, Response
from sqlalchemy.orm import Session
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from database import SessionLocal, User
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


def upsert_user(db: Session, telegram_chat_id: int, moodle_url: str) -> None:
    user = db.get(User, telegram_chat_id)
    if user is None:
        db.add(User(telegram_chat_id=telegram_chat_id, moodle_url=moodle_url))
    else:
        user.moodle_url = moodle_url
    db.commit()


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Welcome to the Moodle Deadline Tracker.\n\n"
        "I monitor your Moodle assignment deadlines and send timely reminders "
        "before they are due.\n\n"
        "To get started, please reply with your Moodle Calendar (.ics) link."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    url = extract_url(update.message.text)
    if not url:
        await update.message.reply_text(
            "Please send a valid Moodle calendar link starting with http:// or https://."
        )
        return

    chat_id = update.effective_chat.id
    db = SessionLocal()

    try:
        upsert_user(db, chat_id, url)
        count = sync_moodle_calendar(db, chat_id, url)
        await update.message.reply_text(
            f"🔄 Sync complete! Found {count} upcoming assignments."
        )
    except requests.RequestException:
        db.rollback()
        await update.message.reply_text(
            "I couldn't fetch that calendar link. Please verify the URL is correct and try again."
        )
    except ValueError:
        db.rollback()
        await update.message.reply_text(
            "That link doesn't appear to be a valid calendar file. Please check the URL and try again."
        )
    except Exception:
        db.rollback()
        await update.message.reply_text(
            "Something went wrong while syncing your calendar. Please try again later."
        )
    finally:
        db.close()


application.add_handler(CommandHandler("start", start_command))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))


@asynccontextmanager
async def lifespan(_: FastAPI):
    if WEBHOOK_URL:
        await application.bot.set_webhook(url=WEBHOOK_URL)
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
