import re
import requests
import urllib3
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from database import Deadline

# Suppress SSL warnings for university self-signed certificates
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def sync_moodle_calendar(db: Session, telegram_chat_id: int, input_data: str) -> int:
    ics_text = ""

    # 1. Fetch from URL or use raw text input
    if input_data.startswith(("http://", "https://")):
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "text/calendar, text/html, application/xhtml+xml, application/xml;q=0.9, */*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
        }
        response = requests.get(input_data, headers=headers, timeout=15, verify=False)
        response.raise_for_status()
        ics_text = response.text
    else:
        ics_text = input_data

    if "BEGIN:VCALENDAR" not in ics_text:
        snippet = ics_text[:120].replace("\n", " ").replace("\r", "")
        raise ValueError(f"Invalid calendar format: '{snippet}...'")

    # 2. Normalize line breaks and folded text lines from Telegram
    normalized_text = ics_text.replace("\r\n", "\n").replace("\r", "\n")

    # 3. Wipe existing deadlines for this chat to prevent duplicate entries
    db.query(Deadline).filter(Deadline.telegram_chat_id == telegram_chat_id).delete()

    # 4. Extract VEVENT blocks
    vevent_blocks = normalized_text.split("BEGIN:VEVENT")
    count = 0

    for block in vevent_blocks[1:]:  # Skip header before first event
        if "END:VEVENT" not in block:
            continue
        
        event_data = block.split("END:VEVENT")[0]

        # Extract Assignment Title / Summary
        summary = "Untitled Assignment"
        summary_match = re.search(r"SUMMARY:(.*?)(?=\n[A-Z\-]+:|\n\n|\Z)", event_data, re.DOTALL)
        if summary_match:
            # Clean multi-line line folding and strip extra spaces
            raw_summary = summary_match.group(1).replace("\n ", "").replace("\n", " ")
            summary = re.sub(r"\s+", " ", raw_summary).strip()

        # Extract Due Date (DTEND or DTSTART)
        dt_match = re.search(r"DTEND:(.*?)\n", event_data) or re.search(r"DTSTART:(.*?)\n", event_data)
        if dt_match:
            raw_dt = dt_match.group(1).strip()
            
            try:
                if "T" in raw_dt:
                    clean_dt = raw_dt.replace("Z", "")
                    # Moodle timestamps are formatted like YYYYMMDDTHHMMSS
                    due_date = datetime.strptime(clean_dt[:15], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
                else:
                    due_date = datetime.strptime(raw_dt[:8], "%Y%m%d").replace(tzinfo=timezone.utc)

                deadline_entry = Deadline(
                    telegram_chat_id=telegram_chat_id,
                    assignment_title=summary,
                    due_date=due_date
                )
                db.add(deadline_entry)
                count += 1
            except Exception as parse_err:
                print(f"Skipping unparseable date '{raw_dt}': {parse_err}")

    db.commit()
    return count
