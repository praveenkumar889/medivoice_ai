import os
import logging
import pytz
from datetime import datetime
from fastapi import APIRouter, HTTPException
from services.supabase_service import (
    get_bookings_for_dashboard, cancel_booking,
    get_pending_reminders, mark_reminder_sent,
    get_all_departments
)
from services.calendar_service import delete_calendar_event
from services.sms_service import send_reminder_sms
from supabase import create_client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/bookings")
async def list_bookings(limit: int = 50):
    """Dashboard: all recent bookings with patient + dept info."""
    return await get_bookings_for_dashboard(limit)


@router.get("/departments")
async def list_departments():
    """List all active hospital departments."""
    return await get_all_departments()


@router.delete("/bookings/{booking_id}")
async def cancel_booking_endpoint(booking_id: str):
    """Cancel a booking and delete the Google Calendar event."""
    client = create_client(
        os.getenv("SUPABASE_URL"),
        os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    )
    result = (
        client.table("bookings")
        .select("*, departments(gcal_id)")
        .eq("id", booking_id)
        .limit(1)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Booking not found")

    booking       = result.data[0]
    gcal_event_id = booking.get("gcal_event_id")
    gcal_id       = booking["departments"]["gcal_id"]

    if gcal_event_id:
        try:
            await delete_calendar_event(gcal_id, gcal_event_id)
        except Exception as e:
            logger.warning(f"Calendar event deletion failed (continuing): {e}")

    return await cancel_booking(booking_id)


@router.post("/reminders/process")
async def process_reminders():
    """
    Process pending reminders due in the next 5 minutes.
    Call this via a cron job every 5 minutes.
    On Railway: set up a cron service or use an external scheduler.
    """
    pending = await get_pending_reminders()
    sent = 0
    failed = 0

    # FIX: Default timezone changed from Asia/Kolkata → America/New_York
    tz = pytz.timezone(os.getenv("TIMEZONE", "America/New_York"))

    for reminder in pending:
        try:
            booking  = reminder["bookings"]
            patient  = booking["patients"]
            dept     = booking["departments"]
            slot_dt  = booking["start_time"]

            dt = datetime.fromisoformat(slot_dt).astimezone(tz)

            await send_reminder_sms(
                to=patient["phone"],
                patient_name=patient["name"] or "there",
                dept=dept["name"],
                slot=dt.strftime("%I:%M %p").lstrip("0"),
                date=dt.strftime("%B %-d"),
                reminder_type=reminder["type"]
            )
            await mark_reminder_sent(reminder["id"])
            sent += 1
        except Exception as e:
            logger.error(f"Reminder {reminder['id']} failed: {e}")
            failed += 1

    return {"processed": len(pending), "sent": sent, "failed": failed}


@router.get("/health/calendar")
async def check_calendar_health():
    """Verify Google Calendar connection is working."""
    try:
        from services.calendar_service import get_calendar_service
        service = await get_calendar_service()
        service.calendarList().list(maxResults=1).execute()
        return {"status": "connected"}
    except RuntimeError as e:
        # Not authorized yet
        return {"status": "not_connected", "detail": str(e)}
    except Exception as e:
        return {"status": "error", "detail": str(e)}
