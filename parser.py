import logging
from datetime import date, datetime, timezone
from io import BytesIO

import requests
from icalendar import Calendar
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import Deadline

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_due_date(raw_dt: datetime | date) -> datetime:
    if isinstance(raw_dt, datetime):
        return _to_utc(raw_dt)
    return datetime(raw_dt.year, raw_dt.month, raw_dt.day, tzinfo=timezone.utc)


def sync_moodle_calendar(db_session: Session, telegram_chat_id: int, url_or_text: str) -> int:
    # Scenario A: User passed a direct web URL link
    if url_or_text.strip().startswith(("http://", "https://")):
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/calendar,text/html"
        }
        response = requests.get(url_or_text.strip(), headers=headers, timeout=30)
        response.raise_for_status()
        calendar_data = response.content
        calendar_text = response.text
    # Scenario B: User manually pasted raw text content
    else:
        calendar_data = url_or_text.encode('utf-8')
        calendar_text = url_or_text

    # Validation 1: Must be an actual calendar file structure
    if "BEGIN:VCALENDAR" not in calendar_text:
        logger.error("Data check failed: Missing BEGIN:VCALENDAR structural boundary.")
        raise ValueError("Invalid calendar payload format structural configuration.")

    # Validation 2: If the calendar structural file is valid but completely empty of events
    if "BEGIN:VEVENT" not in calendar_text:
        logger.info("Calendar file is valid but contains 0 upcoming events.")
        return 0

    calendar = Calendar.from_ical(BytesIO(calendar_data))
    now_utc = datetime.now(timezone.utc)
    synced_count = 0

    for component in calendar.walk():
        if component.name != "VEVENT":
            continue

        summary = component.get("summary")
        dtstart = component.get("dtstart")
        if not summary or not dtstart:
            continue

        title = str(summary)
        due_date = _parse_due_date(dtstart.dt)

        if due_date < now_utc:
            continue

        existing = db_session.scalar(
            select(Deadline).where(
                Deadline.telegram_chat_id == telegram_chat_id,
                Deadline.assignment_title == title,
            )
        )

        if existing is None:
            db_session.add(
                Deadline(
                    telegram_chat_id=telegram_chat_id,
                    assignment_title=title,
                    due_date=due_date,
                )
            )
        elif existing.due_date != due_date:
            existing.due_date = due_date

        synced_count += 1

    db_session.commit()
    return synced_count