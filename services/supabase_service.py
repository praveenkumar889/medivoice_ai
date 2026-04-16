import os
import json
import logging
from supabase import create_client, Client
from typing import Optional, Dict

logger = logging.getLogger(__name__)

# Singleton Supabase client — created once, reused
_client: Optional[Client] = None

def get_client() -> Client:
    global _client
    if _client is None:
        _client = create_client(
            os.getenv("SUPABASE_URL"),
            os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        )
    return _client


# ── Google OAuth tokens ───────────────────────────────────

async def save_gcal_tokens(token_data: dict):
    """Save/update Google Calendar OAuth tokens."""
    client = get_client()
    existing = client.table("gcal_tokens").select("id").limit(1).execute()
    if existing.data:
        client.table("gcal_tokens").update(
            {"token_json": json.dumps(token_data)}
        ).eq("id", existing.data[0]["id"]).execute()
    else:
        client.table("gcal_tokens").insert(
            {"token_json": json.dumps(token_data)}
        ).execute()
    logger.info("Google Calendar tokens saved")


async def get_gcal_tokens() -> dict:
    """Retrieve stored Google Calendar OAuth tokens."""
    client = get_client()
    result = client.table("gcal_tokens").select("token_json").limit(1).execute()
    if not result.data:
        raise RuntimeError(
            "Google Calendar not connected. Visit /auth/google to authorize."
        )
    return json.loads(result.data[0]["token_json"])


# ── Departments ───────────────────────────────────────────

async def get_department_by_name(name: str) -> Optional[Dict]:
    """Case-insensitive department lookup."""
    client = get_client()
    result = (
        client.table("departments")
        .select("*")
        .ilike("name", name)
        .eq("is_active", True)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


async def get_all_departments() -> list:
    client = get_client()
    result = client.table("departments").select("*").eq("is_active", True).execute()
    return result.data


# ── Patients ──────────────────────────────────────────────

async def get_or_create_patient(name: str, phone: str) -> Dict:
    """Find existing patient by phone or create new one."""
    client = get_client()
    existing = (
        client.table("patients")
        .select("*")
        .eq("phone", phone)
        .limit(1)
        .execute()
    )
    if existing.data:
        if name and not existing.data[0].get("name"):
            client.table("patients").update(
                {"name": name}
            ).eq("id", existing.data[0]["id"]).execute()
        return existing.data[0]

    result = client.table("patients").insert(
        {"name": name, "phone": phone}
    ).execute()
    logger.info(f"New patient created — phone ending ...{phone[-4:]}")
    return result.data[0]


async def get_patient_by_phone(phone: str) -> Optional[Dict]:
    client = get_client()
    result = (
        client.table("patients")
        .select("*")
        .eq("phone", phone)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


# ── Bookings ──────────────────────────────────────────────

async def create_booking(data: dict) -> Dict:
    """Insert a new booking record with retry logic."""
    import asyncio
    client = get_client()
    for attempt in range(3):
        try:
            result = client.table("bookings").insert(data).execute()
            logger.info(f"Booking created: {result.data[0]['id']}")
            return result.data[0]
        except Exception as e:
            if attempt == 2:
                logger.error(f"Booking insert failed after 3 attempts: {e}")
                raise
            logger.warning(f"Booking insert attempt {attempt + 1} failed, retrying: {e}")
            await asyncio.sleep(1)


async def check_existing_booking(dept_id: str, start_time: str) -> bool:
    """Check if a slot is already booked for a department (double-booking guard)."""
    client = get_client()
    result = (
        client.table("bookings")
        .select("id")
        .eq("dept_id", dept_id)
        .eq("start_time", start_time)
        .eq("status", "confirmed")
        .limit(1)
        .execute()
    )
    return len(result.data) > 0


async def get_bookings_for_dashboard(limit: int = 50) -> list:
    client = get_client()
    result = (
        client.table("bookings")
        .select("*, patients(name, phone), departments(name)")
        .order("start_time", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data


async def cancel_booking(booking_id: str) -> Dict:
    client = get_client()
    result = (
        client.table("bookings")
        .update({"status": "cancelled"})
        .eq("id", booking_id)
        .execute()
    )
    return result.data[0]


# ── Interactions (call transcripts) ──────────────────────

async def save_interaction(data: dict) -> Dict:
    client = get_client()
    result = client.table("interactions").insert(data).execute()
    return result.data[0]


async def save_transcript(data: dict):
    """Alias for save_interaction — used by transcript pipeline."""
    await save_interaction(data)


# ── Reminders ─────────────────────────────────────────────

async def create_reminders(booking_id: str, slot_start_iso: str):
    """Schedule 24h and 1h SMS reminders for a booking."""
    from datetime import datetime, timedelta
    import pytz

    # FIX: Default changed from Asia/Kolkata → America/New_York
    tz = pytz.timezone(os.getenv("TIMEZONE", "America/New_York"))
    slot_dt = datetime.fromisoformat(slot_start_iso).astimezone(tz)

    reminders = [
        {
            "booking_id":   booking_id,
            "type":         "24h",
            "scheduled_at": (slot_dt - timedelta(hours=24)).isoformat(),
            "status":       "pending"
        },
        {
            "booking_id":   booking_id,
            "type":         "1h",
            "scheduled_at": (slot_dt - timedelta(hours=1)).isoformat(),
            "status":       "pending"
        }
    ]
    client = get_client()
    client.table("reminders").insert(reminders).execute()
    logger.info(f"Reminders scheduled for booking {booking_id}")


async def get_pending_reminders() -> list:
    """Get all reminders due within the next 5 minutes."""
    from datetime import datetime, timedelta
    client = get_client()
    window = (datetime.utcnow() + timedelta(minutes=5)).isoformat()
    result = (
        client.table("reminders")
        .select("*, bookings(*, patients(name, phone), departments(name))")
        .eq("status", "pending")
        .lte("scheduled_at", window)
        .execute()
    )
    return result.data


async def mark_reminder_sent(reminder_id: str):
    from datetime import datetime
    client = get_client()
    client.table("reminders").update({
        "status": "sent",
        "sent_at": datetime.utcnow().isoformat()
    }).eq("id", reminder_id).execute()
