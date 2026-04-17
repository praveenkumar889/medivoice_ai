import os
import json
import logging
import hmac
import hashlib
from fastapi import APIRouter, Request, BackgroundTasks, HTTPException
from pydantic import ValidationError
from dateutil.parser import parse as parse_date
from pytz import timezone

from services.calendar_service import get_available_slots, create_calendar_event, delete_calendar_event
from services.sms_service import send_confirmation_sms
from services.supabase_service import (
    get_or_create_patient, create_booking,
    get_department_by_name, create_reminders,
    check_existing_booking
)
from services.transcription_service import process_recording
from models.schemas import CheckAvailabilityParams, CreateBookingParams

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/vapi", tags=["vapi"])


# ── Security: verify the request is genuinely from Vapi ───
# FIX: Vapi sends the secret as a plain header value (x-vapi-secret),
# NOT as an HMAC signature. The original code used hmac.new() which
# is correct Python but was comparing against the wrong header.
# Vapi dashboard → Assistant → Webhooks → set the same secret string here.

async def verify_vapi_signature(request: Request, body_bytes: bytes) -> bool:
    secret = os.getenv("VAPI_WEBHOOK_SECRET")
    if not secret:
        # No secret configured — skip verification (dev only)
        logger.warning("VAPI_WEBHOOK_SECRET not set — skipping signature check")
        return True

    # Vapi sends the secret directly in x-vapi-secret header
    incoming_secret = request.headers.get("x-vapi-secret", "")
    if incoming_secret:
        return hmac.compare_digest(incoming_secret, secret)
        
    # Vapi Custom Tool UI sends it as a standard Bearer token usually
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
        if hmac.compare_digest(token, secret):
            return True

    # Some Vapi versions use HMAC-SHA256 in x-vapi-signature
    incoming_sig = request.headers.get("x-vapi-signature", "")
    if incoming_sig:
        expected = hmac.new(
            secret.encode(),
            body_bytes,
            hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(incoming_sig, expected)

    # No auth header at all
    logger.warning("Vapi request has no auth header — bypassing for local development test!")
    return True


# ── Helper: parse Vapi payload (handles both formats) ─────
def parse_vapi_payload(body: dict) -> tuple[str, dict]:
    """
    Vapi sends function calls in two formats depending on version:

    FORMAT 1 — Tool call (newer): body has "name" and "parameters" at top level
    FORMAT 2 — Message call (older): body["message"]["functionCall"]["name"]
    FORMAT 3 — Tool calls array: body["message"]["toolCalls"][0]["function"]
    """
    # Format 1: flat top-level
    if "name" in body and "parameters" in body:
        return body["name"], body["parameters"], body.get("toolCallId", "")

    if "message" in body:
        msg = body["message"]

        # Format 2: nested functionCall
        if "functionCall" in msg:
            fc = msg["functionCall"]
            return fc["name"], fc.get("parameters", {}), msg.get("toolCallId", "")

        # Format 3: toolCalls array
        if "toolCalls" in msg and msg["toolCalls"]:
            tc = msg["toolCalls"][0]
            fn_info = tc.get("function", {})
            params = fn_info.get("arguments", {})
            if isinstance(params, str):
                params = json.loads(params)
            return fn_info["name"], params, tc.get("id", "")

    raise ValueError(f"Unrecognised Vapi payload: {list(body.keys())}")


# ─────────────────────────────────────────────────────────────────
# FUNCTION CALL HANDLER
# Vapi calls POST /vapi/function-call mid-conversation for:
#   - checkAvailability  → queries Google Calendar
#   - createBooking      → writes Calendar + DB, sends SMS
# ─────────────────────────────────────────────────────────────────

@router.post("/function-call")
async def handle_function_call(request: Request):
    body_bytes = await request.body()

    if not await verify_vapi_signature(request, body_bytes):
        raise HTTPException(status_code=401, detail="Invalid Vapi signature")

    body = json.loads(body_bytes)
    logger.debug(f"Raw Vapi payload: {json.dumps(body, indent=2)}")

    try:
        fn, params, call_id = parse_vapi_payload(body)
    except (ValueError, KeyError) as e:
        logger.error(f"Could not parse Vapi payload: {e}")
        return {"results": [{"result": "I encountered a technical error. Please try again.", "status": "error"}]}

    resp = await _handle_logic(body, fn, params)
    
    result_obj = {"result": resp.get("result", "")}
    if call_id:
        result_obj["toolCallId"] = call_id
    
    return {"results": [result_obj]}

async def _handle_logic(body: dict, fn: str, params: dict):

    logger.info(f"Vapi function: '{fn}' | params: {params}")

    # US Eastern timezone
    tz = timezone(os.getenv("TIMEZONE", "America/New_York"))

    # ─────────────────────────────────────────────────────
    # checkAvailability
    # ─────────────────────────────────────────────────────
    if fn == "checkAvailability":
        try:
            p = CheckAvailabilityParams(**params)
            dt = parse_date(p.date)
            dt = tz.localize(dt) if dt.tzinfo is None else dt.astimezone(tz)
            date = dt.date().strftime("%Y-%m-%d")
            spoken_date = dt.strftime("%A, %B %d, %Y")
        except ValidationError:
            return {
                "result": "Please tell me both the department name and your preferred date.",
                "status": "success"
            }
        except Exception:
            return {
                "result": "I couldn't understand that date. Could you say it again? For example, 'April 20th' or 'next Monday'.",
                "status": "success"
            }

        dept = await get_department_by_name(p.department)
        if not dept:
            return {
                "result": (
                    f"We don't have a '{p.department}' department. "
                    "Available departments are: Cardiology, Neurology, "
                    "General Medicine, Orthopedics, and Pediatrics. "
                    "Which one did you need?"
                ),
                "status": "success"
            }

        try:
            slots = await get_available_slots(dept["gcal_id"], date)
        except Exception as e:
            logger.error(f"Calendar query failed for {p.department} on {date}: {e}")
            return {
                "result": "I'm having trouble accessing the scheduling system right now. Please try a different date or call back shortly.",
                "status": "success"
            }

        if not slots:
            # Give a more specific reason — weekends vs fully booked
            from pytz import timezone as tz_lookup
            _tz = tz_lookup(os.getenv("TIMEZONE", "Asia/Kolkata"))
            _dt = parse_date(date)
            _dt = _tz.localize(_dt) if _dt.tzinfo is None else _dt
            if _dt.weekday() >= 5:  # Saturday or Sunday
                reason = (
                    f"We are closed on weekends. "
                    f"{spoken_date} is a {'Saturday' if _dt.weekday() == 5 else 'Sunday'}. "
                    f"Our clinic is open Monday to Friday, 9 AM to 5 PM IST. "
                    f"Would you like to book for Monday, {(_dt + __import__('datetime').timedelta(days=(7 - _dt.weekday()))).strftime('%B %d')} instead?"
                )
            else:
                reason = (
                    f"There are no available slots for {dept['name']} on {spoken_date}. "
                    f"Our clinic is open Monday to Friday, 9 AM to 5 PM IST. "
                    f"Would you like to try a different day?"
                )
            return {"result": reason, "status": "success"}

        slot_list = ", ".join(s["start"] for s in slots[:3])
        logger.info(f"Slots for {dept['name']} on {date}: {slot_list}")
        return {
            "result": f"I have the following slots available for {dept['name']} on {spoken_date}: {slot_list}. Which time works best for you?",
            "status": "success"
        }

    # ─────────────────────────────────────────────────────
    # createBooking
    # ─────────────────────────────────────────────────────
    elif fn == "createBooking":
        try:
            p = CreateBookingParams(**params)
            dt = parse_date(p.date)
            dt = tz.localize(dt) if dt.tzinfo is None else dt.astimezone(tz)
            date = dt.date().strftime("%Y-%m-%d")
            spoken_date = dt.strftime("%A, %B %d, %Y")
        except ValidationError as e:
            return {
                "result": "I need a valid department, date, time slot, your full name, and a US phone number to complete the booking.",
                "status": "error"
            }
        except Exception:
            return {
                "result": "I couldn't understand that date. Could you say it again clearly?",
                "status": "error"
            }

        dept = await get_department_by_name(p.department)
        if not dept:
            return {"result": "I couldn't find that department. Please try again.", "status": "error"}

        # Re-fetch slots to get ISO timestamps and verify slot is still free
        try:
            slots = await get_available_slots(dept["gcal_id"], date)
        except Exception as e:
            logger.error(f"Slot re-fetch failed: {e}")
            return {"result": "I had trouble checking the calendar. Please try again.", "status": "error"}

        # Normalise for comparison (strip leading zeros: "09:00 AM" == "9:00 AM")
        def normalise(s: str) -> str:
            return s.strip().lower().replace(" ", "").lstrip("0")

        chosen = next(
            (s for s in slots if normalise(s["start"]) == normalise(p.chosen_slot)),
            None
        )
        if not chosen:
            available = ", ".join(s["start"] for s in slots[:3]) if slots else "none available today"
            return {
                "result": (
                    f"That time slot is no longer available. "
                    f"Current openings are: {available}. Which would you prefer?"
                ),
                "status": "error"
            }

        # Double-booking check at DB level
        if await check_existing_booking(dept_id=dept["id"], start_time=chosen["start_iso"]):
            return {
                "result": "That slot was just taken. Please choose another available time.",
                "status": "error"
            }

        # Write to Google Calendar
        try:
            gcal_event_id = await create_calendar_event(
                gcal_id=dept["gcal_id"],
                patient_name=p.patient_name,
                patient_phone=p.patient_phone,
                dept_name=dept["name"],
                start_iso=chosen["start_iso"],
                end_iso=chosen["end_iso"]
            )
        except Exception as e:
            logger.error(f"Calendar event creation failed: {e}", exc_info=True)
            return {
                "result": "I wasn't able to reserve that time in our system. Please try again.",
                "status": "error"
            }

        # Write to Supabase — rollback calendar event if DB fails
        try:
            patient = await get_or_create_patient(p.patient_name, p.patient_phone)
            booking = await create_booking({
                "patient_id":    patient["id"],
                "dept_id":       dept["id"],
                "start_time":    chosen["start_iso"],
                "end_time":      chosen["end_iso"],
                "gcal_event_id": gcal_event_id,
                "status":        "confirmed",
                "channel":       "voice"
            })
            await create_reminders(booking["id"], chosen["start_iso"])

        except Exception as db_err:
            logger.error(f"DB write failed — rolling back calendar event {gcal_event_id}: {db_err}")
            try:
                await delete_calendar_event(dept["gcal_id"], gcal_event_id)
            except Exception as rollback_err:
                logger.error(f"Rollback also failed: {rollback_err}")
            return {
                "result": "There was a system error saving your booking. Please call the front desk directly.",
                "status": "error"
            }

        # Send SMS confirmation (non-blocking — booking already confirmed)
        try:
            await send_confirmation_sms(
                to=p.patient_phone,
                patient_name=p.patient_name,
                dept=dept["name"],
                slot=p.chosen_slot,
                date=date
            )
        except Exception as sms_err:
            # Log but never fail the booking over SMS
            logger.warning(f"SMS failed (booking still confirmed): {sms_err}")

        logger.info(
            f"Booking confirmed | id={booking['id']} | dept={dept['name']} | "
            f"date={date} | slot={p.chosen_slot}"
        )
        return {
            "result": (
                f"You're all set, {p.patient_name}! "
                f"Your {dept['name']} appointment is confirmed for {spoken_date} at {p.chosen_slot}. "
                f"You'll receive a text confirmation shortly. "
                f"Thank you for calling {os.getenv('HOSPITAL_NAME', 'City Hospital')}!"
            ),
            "status": "success"
        }

    logger.warning(f"Unknown Vapi function called: '{fn}'")
    return {"result": "I didn't understand that request. Please try again.", "status": "error"}


# ─────────────────────────────────────────────────────────────────
# END OF CALL HANDLER
# Vapi calls POST /vapi/end-of-call when the call ends.
# Triggers Whisper transcription as a background task.
# ─────────────────────────────────────────────────────────────────

@router.post("/end-of-call")
async def handle_end_of_call(request: Request, background_tasks: BackgroundTasks):
    body_bytes = await request.body()

    if not await verify_vapi_signature(request, body_bytes):
        raise HTTPException(status_code=401, detail="Invalid Vapi signature")

    body = json.loads(body_bytes)
    msg  = body.get("message", {})

    recording_url = msg.get("recordingUrl")
    duration      = msg.get("durationSeconds", 0)
    call_id       = msg.get("call", {}).get("id", "unknown")
    phone         = msg.get("call", {}).get("customer", {}).get("number")

    logger.info(
        f"Call ended | id={call_id} | duration={duration}s | "
        f"recording={'yes' if recording_url else 'no'}"
    )

    if recording_url:
        background_tasks.add_task(
            process_recording,
            recording_url=recording_url,
            call_id=call_id,
            patient_phone=phone,
            duration=duration
        )

    return {"status": "received"}
