# 🏥 MediVoice AI — Hospital Voice Appointment Booking System

A production-grade, AI-powered voice assistant that allows patients to book hospital appointments by simply calling and speaking naturally. Built with **Vapi**, **FastAPI**, **Google Calendar**, **Supabase**, and **Twilio**.

---

## 📌 Table of Contents

1. [What This System Does](#1-what-this-system-does)
2. [System Architecture](#2-system-architecture)
3. [End-to-End Call Flow](#3-end-to-end-call-flow)
4. [Project Structure](#4-project-structure)
5. [File-by-File Breakdown](#5-file-by-file-breakdown)
6. [Database Schema](#6-database-schema)
7. [Environment Variables](#7-environment-variables)
8. [Installation & Local Setup](#8-installation--local-setup)
9. [Google OAuth Setup](#9-google-oauth-setup)
10. [Vapi Dashboard Configuration](#10-vapi-dashboard-configuration)
11. [API Endpoints Reference](#11-api-endpoints-reference)
12. [Production Deployment](#12-production-deployment)
13. [Known Limitations & Future Improvements](#13-known-limitations--future-improvements)

---

## 1. What This System Does

A patient calls the hospital's AI phone number. A natural voice conversation plays out:

```
AI:      "Hello, welcome to MediVoice AI. How can I assist you today?"
Patient: "I want to book an appointment."
AI:      "Which department? Cardiology, Neurology, General Medicine, Orthopedics, or Pediatrics?"
Patient: "Cardiology."
AI:      "What date do you prefer?"
Patient: "Tomorrow."
AI:      "Please hold on, I will check available slots for you..."
         [calls your Python backend → checks Google Calendar]
AI:      "I have slots available for Cardiology on Friday, April 18, 2026:
          9:00 AM, 9:30 AM, and 10:00 AM. Which time works best for you?"
Patient: "10 AM."
AI:      "Could I get your full name and phone number to secure the booking?"
Patient: "Praveen Kumar, 8897729577."
AI:      "Perfect, let me confirm your booking now..."
         [calls Python backend → creates Google Calendar event → saves to Supabase]
AI:      "You're all set, Praveen! Your Cardiology appointment is confirmed for
          Friday, April 18, 2026 at 10:00 AM. Thank you for calling!"
```

The moment the call ends:
- A **Google Calendar event** is created with the patient's details.
- A **Supabase database record** is written for the booking.
- **SMS reminders** are scheduled (24h and 1h before the appointment).
- The call recording is **transcribed** by OpenAI Whisper and saved.

---

## 2. System Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           PATIENT'S PHONE                               │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │ Voice Call
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                            VAPI PLATFORM                                │
│  ┌──────────────┐  ┌───────────────┐  ┌──────────────────────────────┐ │
│  │  Deepgram    │  │   GPT-4.1     │  │   Vapi TTS (Elliot voice)    │ │
│  │  (Speech→    │  │   (AI brain   │  │   (Text → Speech back to     │ │
│  │   Text STT)  │  │    + logic)   │  │    the patient)              │ │
│  └──────────────┘  └───────┬───────┘  └──────────────────────────────┘ │
│                            │ Tool calls (checkAvailability/createBooking)│
└────────────────────────────┼────────────────────────────────────────────┘
                             │ HTTPS POST
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     YOUR FASTAPI BACKEND (Python)                       │
│                                                                         │
│  POST /vapi/function-call                                               │
│  ├── checkAvailability → queries Google Calendar freebusy API           │
│  └── createBooking    → writes Calendar event + Supabase record         │
│                                                                         │
│  POST /vapi/end-of-call                                                 │
│  └── downloads recording → Whisper transcription → saves to Supabase   │
└──────────┬──────────────────────────────┬───────────────────────────────┘
           │                              │
           ▼                              ▼
┌─────────────────────┐       ┌─────────────────────────┐
│   GOOGLE CALENDAR   │       │        SUPABASE          │
│                     │       │                          │
│  • Check free slots │       │  patients table          │
│  • Create events    │       │  bookings table          │
│  • Delete events    │       │  departments table       │
│  • 24h/1h reminders │       │  reminders table         │
└─────────────────────┘       │  gcal_tokens table       │
                              │  interactions table      │
                              └─────────────────────────┘
```

---

## 3. End-to-End Call Flow

### Phase 1: Availability Check

```
Patient says department + date
         ↓
Vapi extracts params → calls POST /vapi/function-call
         ↓
vapi_webhook.py receives { "name": "checkAvailability", "date": "2026-04-18", "department": "Cardiology" }
         ↓
supabase_service.py → SELECT * FROM departments WHERE name ilike 'Cardiology'
         ↓                Returns { id, gcal_id: "primary", ... }
calendar_service.py → Google Calendar freebusy API query for that gcal_id on that date
         ↓                Returns list of busy time blocks
Python calculates free 30-min slots between 9 AM–5 PM (skips weekends)
         ↓
Returns to Vapi: { "results": [{ "toolCallId": "...", "result": "Slots: 9:00 AM, 9:30 AM, 10:00 AM" }] }
         ↓
Vapi reads the slots aloud to the patient
```

### Phase 2: Booking Creation

```
Patient picks a time + gives name + phone
         ↓
Vapi calls POST /vapi/function-call
         ↓
vapi_webhook.py receives { "name": "createBooking", "department": "Cardiology",
                           "date": "2026-04-18", "chosen_slot": "10:00 AM",
                           "patient_name": "Praveen", "patient_phone": "8897729577" }
         ↓
Re-fetches slots → verifies chosen slot is still free
         ↓
Double booking check → SELECT FROM bookings WHERE start_time = chosen_time
         ↓
calendar_service.py → Creates Google Calendar event with patient name & phone in description
         ↓
supabase_service.py → GET or CREATE patient record
                    → INSERT booking record (status: confirmed)
                    → INSERT two reminders (24h email, 1h popup)
         ↓
sms_service.py → Sends SMS confirmation via Twilio (skipped if Twilio not configured)
         ↓
Returns: "You're all set, Praveen! Cardiology confirmed for Friday, April 18, 2026 at 10:00 AM."
```

### Phase 3: Post-Call Transcription (Background)

```
Call ends
    ↓
Vapi sends POST /vapi/end-of-call with recordingUrl
    ↓
transcription_service.py downloads audio
    ↓
OpenAI Whisper converts audio → text
    ↓
Saves transcript to Supabase interactions table
```

---

## 4. Project Structure

```
medivoice_ai/
│
├── main.py                          # FastAPI app entry point
├── requirements.txt                 # Python dependencies
├── supabase_schema.sql              # Database schema (run once in Supabase)
├── .env                             # Your real secrets (never commit!)
├── .env.example                     # Safe template for new developers
├── .gitignore                       # Excludes .env, .venv, __pycache__ etc.
│
├── routers/
│   ├── vapi_webhook.py              # Core: handles all Vapi tool calls
│   ├── auth.py                      # Google OAuth2 login flow
│   └── admin.py                     # Admin REST API (list/cancel bookings, reminders)
│
├── services/
│   ├── calendar_service.py          # Google Calendar: slots + event creation
│   ├── supabase_service.py          # All Supabase database operations
│   ├── sms_service.py               # Twilio SMS confirmations & reminders
│   └── transcription_service.py     # OpenAI Whisper post-call transcription
│
└── models/
    └── schemas.py                   # Pydantic data models & validation
```

---

## 5. File-by-File Breakdown

### `main.py` — Application Entry Point
- Loads `.env` variables **before** any other imports (critical for correct timezone loading).
- Validates that `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `VAPI_WEBHOOK_SECRET`, and `OPENAI_API_KEY` exist at startup — crashes intentionally if missing.
- Registers three routers: `vapi_webhook`, `auth`, `admin`.
- Sets up `PIIFilter` on logging to automatically redact phone numbers from logs.
- Exposes a `/health` endpoint for uptime monitoring.

---

### `routers/vapi_webhook.py` — Core Business Logic
The heart of the system. Handles all incoming Vapi requests.

**Security:**
- Verifies every request using `x-vapi-secret` header against `VAPI_WEBHOOK_SECRET` in `.env`.
- Rejects unauthorized requests with `401 Unauthorized`.

**Endpoints:**
- `POST /vapi/function-call` — Routes to `checkAvailability` or `createBooking`.
- `POST /vapi/end-of-call` — Triggers background Whisper transcription.

**`checkAvailability` logic:**
1. Parses date string (handles "tomorrow", "April 20", etc.) using `dateutil`.
2. Formats `spoken_date` as "Friday, April 18, 2026" for natural TTS output.
3. Queries Supabase for the department's `gcal_id`.
4. Calls `calendar_service.get_available_slots()`.
5. Returns top 3 slots or a descriptive error (e.g., "We are closed on weekends. April 18 is a Saturday...").

**`createBooking` logic:**
1. Re-fetches live calendar slots to verify the chosen slot is still available.
2. Performs double-booking check at the database level.
3. Creates Google Calendar event with 24h email + 1h popup reminder.
4. Gets or creates patient record in Supabase.
5. Writes booking record with status `confirmed`.
6. Schedules 2 reminder rows in the `reminders` table.
7. Attempts SMS confirmation (gracefully skips if Twilio not configured).
8. Returns confirmation message with `spoken_date` format.

**Response format (required by Vapi):**
```json
{
  "results": [
    {
      "toolCallId": "call_abc123",
      "result": "I have slots available for Cardiology on Friday, April 18..."
    }
  ]
}
```

---

### `routers/auth.py` — Google OAuth2
- `GET /auth/login` — Redirects to Google's OAuth consent screen.
- `GET /auth/callback` — Receives the authorization code, exchanges it for tokens, and saves them to Supabase `gcal_tokens` table.
- This must be done **once** before the system can read/write Google Calendar.

---

### `routers/admin.py` — Admin API
| Endpoint | Method | Description |
|---|---|---|
| `/admin/bookings` | GET | List recent bookings with patient + department info |
| `/admin/departments` | GET | List all active hospital departments |
| `/admin/bookings/{id}` | DELETE | Cancel booking + delete Google Calendar event |
| `/admin/reminders/process` | POST | Send pending SMS reminders (call via cron job) |
| `/admin/health/calendar` | GET | Verify Google Calendar connection is working |

---

### `services/calendar_service.py` — Google Calendar Integration
- **Token caching:** Stores authenticated Google credentials in memory (`_cached_creds`). Only refreshes from Supabase when the token is within 5 minutes of expiry. This eliminates the 24-second auth latency on every request.
- `get_available_slots(gcal_id, date)` — Queries the Calendar freebusy API, generates 30-minute slots from 9 AM to 5 PM, skips weekends, and returns slots not already blocked.
- `create_calendar_event(...)` — Creates a calendar event with patient name, phone, department in the description. Includes 24h email + 1h popup reminders.
- `delete_calendar_event(gcal_id, event_id)` — Used by admin cancellation and as rollback if database write fails after Calendar event creation.

---

### `services/supabase_service.py` — Database Operations
All database interactions go through this service.

Key functions:
- `get_department_by_name(name)` — Case-insensitive department lookup.
- `get_or_create_patient(name, phone)` — Upsert patient by phone number.
- `create_booking(data)` — Insert new booking record.
- `check_existing_booking(dept_id, start_time)` — Double-booking prevention.
- `create_reminders(booking_id, start_iso)` — Schedule 24h and 1h reminders.
- `get_bookings_for_dashboard(limit)` — Admin dashboard query with joins.
- `cancel_booking(booking_id)` — Sets booking status to `cancelled`.
- `get_gcal_tokens()` / `save_gcal_tokens()` — Read/write OAuth tokens for Google Calendar.

---

### `services/sms_service.py` — Twilio SMS
- Sends booking confirmation SMS immediately after booking.
- Sends reminder SMS (triggered by admin cron endpoint).
- Gracefully skips all SMS operations if `TWILIO_ACCOUNT_SID` is not set — logs a warning instead of crashing.

---

### `services/transcription_service.py` — Call Recording Transcription
- Called as a **background task** after every call ends.
- Downloads the call audio from Vapi's recording URL.
- Sends it to OpenAI Whisper API for speech-to-text conversion.
- Saves the full transcript to Supabase `interactions` table.
- Completely optional — if `OPENAI_API_KEY` is missing, it saves the recording URL only.

---

### `models/schemas.py` — Pydantic Models
Data validation models used throughout the app:
- `CheckAvailabilityParams` — Validates `department` and `date` from Vapi.
- `CreateBookingParams` — Validates department, date, chosen_slot, patient_name, and `patient_phone` (regex: 10–15 digits).
- `TimeSlot`, `Department`, `Patient`, `Booking` — Typed data structures.

---

## 6. Database Schema

Run `supabase_schema.sql` once in the Supabase SQL Editor to create all tables.

```
gcal_tokens          — Stores Google OAuth2 token (one row)
departments          — Hospital departments, each linked to a Google Calendar ID
patients             — Patient records keyed by phone number
bookings             — Appointment bookings with status tracking
reminders            — Scheduled SMS reminders (pending/sent/failed)
interactions         — Call transcripts from Whisper ASR
```

---

## 7. Environment Variables

Copy `.env.example` to `.env` and fill in real values.

| Variable | Required | Description |
|---|---|---|
| `SUPABASE_URL` | ✅ | Your Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | ✅ | Supabase admin key (full DB access) |
| `VAPI_WEBHOOK_SECRET` | ✅ | Secret to verify Vapi requests |
| `OPENAI_API_KEY` | ✅ | For Whisper transcription after calls |
| `GOOGLE_CLIENT_ID` | ✅ | Google OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | ✅ | Google OAuth client secret |
| `GOOGLE_REDIRECT_URI` | ✅ | OAuth callback URL |
| `TWILIO_ACCOUNT_SID` | ⚠️ Optional | Twilio for SMS (skipped if missing) |
| `TWILIO_AUTH_TOKEN` | ⚠️ Optional | Twilio authentication |
| `TWILIO_PHONE_NUMBER` | ⚠️ Optional | Sender phone number for SMS |
| `TIMEZONE` | ✅ | e.g. `Asia/Kolkata` |
| `HOSPITAL_NAME` | ✅ | Spoken in confirmation messages |
| `PORT` | ✅ | Server port (default: 8000) |
| `ENVIRONMENT` | Optional | `development` enables Swagger docs |

---

## 8. Installation & Local Setup

### Prerequisites
- Python 3.9+
- A [Vapi](https://vapi.ai) account
- A [Supabase](https://supabase.com) project
- A [Google Cloud](https://console.cloud.google.com) project with Calendar API enabled
- [ngrok](https://ngrok.com) for local tunneling (development only)

### Steps

```bash
# 1. Clone the repo
git clone https://github.com/praveenkumar889/medivoice_ai.git
cd medivoice_ai

# 2. Create virtual environment
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Mac/Linux

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
copy .env.example .env
# Edit .env with your real credentials

# 5. Set up Supabase database
# → Go to Supabase Dashboard > SQL Editor
# → Paste and run the contents of supabase_schema.sql

# 6. Start the server
uvicorn main:app --reload

# 7. Start ngrok tunnel (new terminal)
ngrok http 8000
```

---

## 9. Google OAuth Setup

This must be done **once** to authorize Google Calendar access.

1. Go to `http://127.0.0.1:8000/auth/login` in your browser.
2. Sign in with the Google account that owns your hospital calendar.
3. Grant Calendar access permissions.
4. You'll be redirected back — tokens are automatically saved to Supabase `gcal_tokens` table.
5. Verify it worked: `http://127.0.0.1:8000/admin/health/calendar` should return `{"status": "connected"}`.

---

## 10. Vapi Dashboard Configuration

### Assistant Settings
- **Provider:** OpenAI | **Model:** GPT-4.1
- **Voice:** Vapi — Elliot
- **First Message:** `Hello, welcome to MediVoice AI. How can I assist you today?`

### System Prompt
```
You are a hospital appointment booking assistant.

PHASE 1: CHECKING AVAILABILITY
1. Ask: "Which department do you need? (Cardiology, Neurology, General Medicine, Orthopedics, Pediatrics)"
2. After user answers, ask: "What date do you prefer?"
3. As soon as user provides BOTH → IMMEDIATELY call tool "checkAvailability"
4. Read exact time slots back to the user and ask which they prefer.

PHASE 2: CREATING THE BOOKING
5. Once user picks a slot, ask: "Could I please get your full name and phone number?"
6. As soon as user provides both → IMMEDIATELY call tool "createBooking"

IMPORTANT CONTEXT: Today's date is {{date}} in Indian Standard Time.
Format all dates as YYYY-MM-DD when calling tools.
Valid departments: Cardiology, Neurology, Orthopedics, Pediatrics, General Medicine.
```

### Tools — `checkAvailability`
- **Type:** Function
- **Server URL:** `https://your-ngrok-url.ngrok-free.app/vapi/function-call`
- **Headers:** `Content-Type: application/json`, `x-vapi-secret: your-webhook-secret`
- **Timeout:** 30 seconds
- **Parameters:** `department` (string), `date` (string, YYYY-MM-DD)
- **Request Start Message:** `"Please hold on, I will check the available slots for you."`

### Tools — `createBooking`
- **Type:** Function
- **Server URL:** same as above
- **Timeout:** 30 seconds
- **Parameters:** `department`, `date`, `chosen_slot`, `patient_name`, `patient_phone`
- **Request Start Message:** `"Perfect, let me confirm your booking now. Just a moment..."`

---

## 11. API Endpoints Reference

| Method | Endpoint | Description |
|---|---|---|
| GET | `/health` | System health check |
| GET | `/auth/login` | Start Google OAuth flow |
| GET | `/auth/callback` | Google OAuth callback |
| POST | `/vapi/function-call` | Vapi tool call handler (main endpoint) |
| POST | `/vapi/end-of-call` | Post-call transcription trigger |
| GET | `/admin/bookings` | List all bookings |
| GET | `/admin/departments` | List all departments |
| DELETE | `/admin/bookings/{id}` | Cancel a booking |
| POST | `/admin/reminders/process` | Send pending SMS reminders |
| GET | `/admin/health/calendar` | Check Google Calendar connection |

> **Swagger UI:** Available at `/docs` only when `ENVIRONMENT=development` is set.

---

## 12. Production Deployment

Replace Ngrok with a permanent cloud host:

### Option A: Railway
```bash
# Install Railway CLI
npm install -g @railway/cli
railway login
railway init
railway up
```
Set all environment variables in Railway Dashboard → Variables.

### Option B: Render
1. Connect your GitHub repo to [render.com](https://render.com).
2. Create a **Web Service** with:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `uvicorn main:app --host 0.0.0.0 --port $PORT`
3. Add all environment variables in Render Dashboard.

**After deployment:** Update the Server URL in both Vapi tools from the Ngrok URL to your permanent production URL.

---

## 13. Known Limitations & Future Improvements

| Limitation | Proposed Fix |
|---|---|
| All departments share one Google Calendar | Create separate Google Calendars per department |
| No patient authentication | Add OTP verification on phone number |
| Twilio SMS not configured | Add Twilio credentials for real SMS |
| ngrok URL changes on restart | Deploy to Railway/Render for permanent URL |
| Python 3.9 (some Google libs deprecated) | Upgrade to Python 3.11+ |
| No dashboard UI | Build admin React dashboard using `/admin` APIs |
| No cancellation via voice | Add `cancelBooking` Vapi tool |
| No rescheduling via voice | Add `rescheduleBooking` Vapi tool |

---

## Tech Stack Summary

| Component | Technology |
|---|---|
| Voice AI Platform | Vapi (GPT-4.1 + Deepgram STT + Vapi TTS) |
| Backend Framework | FastAPI (Python 3.9) |
| Database | Supabase (PostgreSQL) |
| Calendar | Google Calendar API v3 |
| SMS | Twilio Programmable Messaging |
| Transcription | OpenAI Whisper |
| Tunneling (Dev) | Ngrok |
| Hosting (Prod) | Railway / Render |

---

*Built with ❤️ — MediVoice AI v1.0.0*
