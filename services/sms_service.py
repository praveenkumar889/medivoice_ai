import os
import logging
from twilio.rest import Client

logger = logging.getLogger(__name__)


from typing import Optional

def get_twilio_client() -> Optional[Client]:
    """
    Return Twilio client if credentials are configured, else None.
    During Vapi Talk dev mode (no real phone calls), Twilio is optional.
    SMS will be skipped gracefully if credentials are absent.
    """
    sid   = os.getenv("TWILIO_ACCOUNT_SID")
    token = os.getenv("TWILIO_AUTH_TOKEN")
    if not sid or not token:
        return None
    return Client(sid, token)


def _sms_enabled() -> bool:
    return bool(
        os.getenv("TWILIO_ACCOUNT_SID") and
        os.getenv("TWILIO_AUTH_TOKEN") and
        os.getenv("TWILIO_PHONE_NUMBER")
    )


async def send_confirmation_sms(
    to: str,
    patient_name: str,
    dept: str,
    slot: str,
    date: str
):
    """
    Send booking confirmation SMS immediately after booking.
    Silently skips if Twilio is not configured (dev mode).
    """
    if not _sms_enabled():
        logger.info(f"SMS skipped (Twilio not configured) — would have sent confirmation to ...{to[-4:]}")
        return

    hospital = os.getenv("HOSPITAL_NAME", "City Hospital")
    timezone_abbr = _get_timezone_abbr()

    message = (
        f"Hi {patient_name}, your {dept} appointment at {hospital} "
        f"is confirmed for {date} at {slot} ({timezone_abbr}). "
        f"Reply CANCEL to cancel. Questions? Call us back."
    )
    try:
        client = get_twilio_client()
        msg = client.messages.create(
            body=message,
            from_=os.getenv("TWILIO_PHONE_NUMBER"),
            to=to
        )
        logger.info(f"Confirmation SMS sent | sid={msg.sid} | to=...{to[-4:]}")
    except Exception as e:
        # Don't raise — booking is confirmed, SMS failure is non-fatal
        logger.error(f"Confirmation SMS failed to ...{to[-4:]}: {e}")


async def send_reminder_sms(
    to: str,
    patient_name: str,
    dept: str,
    slot: str,
    date: str,
    reminder_type: str  # "24h" or "1h"
):
    """Send 24h or 1h reminder SMS."""
    if not _sms_enabled():
        logger.info(f"Reminder SMS skipped (Twilio not configured) — {reminder_type} for ...{to[-4:]}")
        return

    hospital = os.getenv("HOSPITAL_NAME", "City Hospital")
    timezone_abbr = _get_timezone_abbr()

    if reminder_type == "24h":
        message = (
            f"Reminder: Hi {patient_name}, your {dept} appointment at "
            f"{hospital} is tomorrow, {date} at {slot} ({timezone_abbr}). "
            f"Reply CANCEL to cancel."
        )
    else:
        message = (
            f"Reminder: Hi {patient_name}, your {dept} appointment at "
            f"{hospital} is in 1 hour — {slot} ({timezone_abbr}). See you soon!"
        )

    try:
        client = get_twilio_client()
        msg = client.messages.create(
            body=message,
            from_=os.getenv("TWILIO_PHONE_NUMBER"),
            to=to
        )
        logger.info(f"{reminder_type} reminder sent | sid={msg.sid}")
    except Exception as e:
        logger.error(f"Reminder SMS failed: {e}")


def _get_timezone_abbr() -> str:
    """Return a readable timezone abbreviation for SMS messages."""
    tz_map = {
        "America/New_York":    "ET",
        "America/Chicago":     "CT",
        "America/Denver":      "MT",
        "America/Los_Angeles": "PT",
        "America/Phoenix":     "MT",
    }
    return tz_map.get(os.getenv("TIMEZONE", "America/New_York"), "ET")
