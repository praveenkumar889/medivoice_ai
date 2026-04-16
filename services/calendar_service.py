import os
import json
import asyncio
import logging
import pytz
from datetime import datetime, timedelta
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from services.supabase_service import get_gcal_tokens, save_gcal_tokens

logger = logging.getLogger(__name__)

TIMEZONE      = os.getenv("TIMEZONE", "Asia/Kolkata")
SLOT_DURATION = 30   # minutes per appointment slot
WORKING_START = 9    # 9:00 AM
WORKING_END   = 17   # 5:00 PM

# ── In-memory credential cache ─────────────────────────────────────────────
# Avoids fetching + re-authenticating from Supabase on every single request.
# The cached creds are reused until they are within 5 minutes of expiry.
_cached_creds = None

async def get_calendar_service():
    """Build authenticated Google Calendar service with in-memory token caching."""
    global _cached_creds

    # Reuse cached credentials if they are still valid (not expiring within 5 min)
    if _cached_creds and _cached_creds.valid:
        expiry = _cached_creds.expiry
        if expiry is None or (expiry - datetime.utcnow()).total_seconds() > 300:
            return build("calendar", "v3", credentials=_cached_creds, cache_discovery=False)

    # Cache miss — fetch from Supabase and refresh if needed
    token_data = await get_gcal_tokens()
    creds = Credentials(
        token=token_data["token"],
        refresh_token=token_data["refresh_token"],
        token_uri=token_data["token_uri"],
        client_id=token_data["client_id"],
        client_secret=token_data["client_secret"],
    )
    if not creds.valid and creds.refresh_token:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: creds.refresh(Request()))
        await save_gcal_tokens(json.loads(creds.to_json()))
        logger.info("Google Calendar token refreshed and cached")

    _cached_creds = creds
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _format_slot_time(dt: datetime) -> str:
    """
    Format a datetime to "10:30 AM" cross-platform.
    FIX: %-I is Linux-only and crashes on Windows.
    Using %I and lstrip("0") works on all platforms.
    """
    return dt.strftime("%I:%M %p").lstrip("0")


async def get_available_slots(gcal_id: str, date: str) -> list:
    """
    Returns up to 6 available 30-minute slots for a department calendar.

    Args:
        gcal_id: Google Calendar ID for the department
        date:    Date string in YYYY-MM-DD format

    Returns:
        List of slot dicts: [{start, start_iso, end_iso}]
    """
    try:
        service = await get_calendar_service()
        tz = pytz.timezone(TIMEZONE)

        day_start = tz.localize(
            datetime.strptime(f"{date} {WORKING_START:02d}:00", "%Y-%m-%d %H:%M")
        )
        day_end = tz.localize(
            datetime.strptime(f"{date} {WORKING_END:02d}:00", "%Y-%m-%d %H:%M")
        )

        # Skip weekends (0=Mon, 6=Sun)
        if day_start.weekday() >= 5:
            return []

        # Run Google API call in executor (it's synchronous)
        loop = asyncio.get_event_loop()
        freebusy_result = await loop.run_in_executor(
            None,
            lambda: service.freebusy().query(body={
                "timeMin": day_start.isoformat(),
                "timeMax": day_end.isoformat(),
                "timeZone": TIMEZONE,
                "items": [{"id": gcal_id}]
            }).execute()
        )

        busy_periods = freebusy_result["calendars"].get(gcal_id, {}).get("busy", [])

        busy_ranges = [
            (
                datetime.fromisoformat(b["start"]).astimezone(tz),
                datetime.fromisoformat(b["end"]).astimezone(tz)
            )
            for b in busy_periods
        ]

        available_slots = []
        current = day_start

        while current + timedelta(minutes=SLOT_DURATION) <= day_end:
            slot_end = current + timedelta(minutes=SLOT_DURATION)

            is_busy = any(
                current < b_end and slot_end > b_start
                for b_start, b_end in busy_ranges
            )

            if not is_busy:
                available_slots.append({
                    "start":     _format_slot_time(current),  # "10:30 AM"
                    "start_iso": current.isoformat(),
                    "end_iso":   slot_end.isoformat()
                })

            current += timedelta(minutes=SLOT_DURATION)

        logger.info(f"Found {len(available_slots)} slots for calendar {gcal_id[:20]}... on {date}")
        return available_slots[:6]

    except Exception as e:
        logger.error(f"Calendar slot fetch failed for {date}: {e}", exc_info=True)
        raise


async def create_calendar_event(
    gcal_id: str,
    patient_name: str,
    patient_phone: str,
    dept_name: str,
    start_iso: str,
    end_iso: str
) -> str:
    """Creates a Google Calendar event and returns the event ID."""
    service = await get_calendar_service()

    event_body = {
        "summary": f"{dept_name} — {patient_name}",
        "description": (
            f"Patient: {patient_name}\n"
            f"Phone: {patient_phone}\n"
            f"Booked via: Voice AI\n"
            f"Department: {dept_name}"
        ),
        "start": {"dateTime": start_iso, "timeZone": TIMEZONE},
        "end":   {"dateTime": end_iso,   "timeZone": TIMEZONE},
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "email",  "minutes": 1440},  # 24h before
                {"method": "popup",  "minutes": 60},    # 1h before
            ]
        }
    }

    loop = asyncio.get_event_loop()
    for attempt in range(3):
        try:
            event = await loop.run_in_executor(
                None,
                lambda: service.events().insert(
                    calendarId=gcal_id,
                    body=event_body
                ).execute()
            )
            logger.info(f"Calendar event created: {event['id']}")
            return event["id"]
        except Exception as e:
            if attempt == 2:
                logger.error(f"Calendar event creation failed after 3 attempts: {e}")
                raise
            logger.warning(f"Calendar event creation attempt {attempt + 1} failed, retrying: {e}")
            await asyncio.sleep(1)


async def delete_calendar_event(gcal_id: str, event_id: str):
    """Deletes a Google Calendar event (used for cancellations and rollback)."""
    service = await get_calendar_service()
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        lambda: service.events().delete(calendarId=gcal_id, eventId=event_id).execute()
    )
    logger.info(f"Calendar event deleted: {event_id}")
