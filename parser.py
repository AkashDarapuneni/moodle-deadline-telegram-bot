from datetime import date, datetime, timezone
from io import BytesIO

import requests
from icalendar import Calendar
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import Deadline


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_due_date(raw_dt: datetime | date) -> datetime:
    if isinstance(raw_dt, datetime):
        return _to_utc(raw_dt)
    return datetime(raw_dt.year, raw_dt.month, raw_dt.day, tzinfo=timezone.utc)


def sync_moodle_calendar(db_session: Session, telegram_chat_id: int, url: str) -> int:
    response = requests.get(url, timeout=30)
    response.raise_for_status()

    calendar = Calendar.from_ical(BytesIO(response.content))
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
