import asyncio
import logging
import os
import time  # For startup delay
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import select
from telegram import Bot
from telegram.error import TelegramError

# Ensure these match your database.py file exactly
from database import Deadline, SessionLocal, engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
bot = Bot(token=TELEGRAM_BOT_TOKEN)


async def _send_alert(chat_id: int, text: str) -> bool:
    try:
        await bot.send_message(chat_id=chat_id, text=text)
        return True
    except TelegramError as exc:
        logger.warning("Telegram API error for chat %s: %s", chat_id, exc)
        return False


async def _process_deadlines(deadlines: list[Deadline], now_utc: datetime) -> None:
    for deadline in deadlines:
        time_remaining = deadline.due_date - now_utc
        title = deadline.assignment_title
        chat_id = deadline.telegram_chat_id

        if (
            timedelta(hours=6) < time_remaining <= timedelta(hours=24)
            and not deadline.sent_24h_alert
        ):
            sent = await _send_alert(
                chat_id,
                f"⏰ Reminder: \"{title}\" is due in less than 24 hours.",
            )
            if sent:
                deadline.sent_24h_alert = True

        elif (
            timedelta(hours=1) < time_remaining <= timedelta(hours=6)
            and not deadline.sent_6h_alert
        ):
            sent = await _send_alert(
                chat_id,
                f"⚠️ Reminder: \"{title}\" is due in less than 6 hours.",
            )
            if sent:
                deadline.sent_6h_alert = True

        elif (
            timedelta(hours=0) < time_remaining <= timedelta(hours=1)
            and not deadline.sent_1h_alert
        ):
            sent = await _send_alert(
                chat_id,
                f"🚨 Final reminder: \"{title}\" is due in less than 1 hour!",
            )
            if sent:
                deadline.sent_1h_alert = True


def check_and_send_alerts() -> None:
    db = SessionLocal()
    now_utc = datetime.now(timezone.utc)

    try:
        deadlines = db.scalars(
            select(Deadline).where(Deadline.due_date > now_utc)
        ).all()
        asyncio.run(_process_deadlines(deadlines, now_utc))
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Failed to check and send alerts")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    logger.info("Starting alert worker - waiting 10 seconds for database initialization...")
    time.sleep(10)  # Gives Uvicorn time to create tables first
    
    scheduler = BlockingScheduler()
    scheduler.add_job(check_and_send_alerts, "interval", minutes=5)
    
    # Run an immediate check once the sleep completes
    check_and_send_alerts()
    scheduler.start()