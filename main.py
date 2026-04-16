import os
import re
import logging
from dotenv import load_dotenv

load_dotenv(override=True)

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from routers import vapi_webhook, auth, admin

# ── Env validation ────────────────────────────────────────
# Only fail hard on vars the system cannot function without at all.
# Twilio is optional during Vapi Talk dev mode — SMS is skipped gracefully.
REQUIRED_ENV = [
    "SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
    "VAPI_WEBHOOK_SECRET",
    "OPENAI_API_KEY",
]

missing = [key for key in REQUIRED_ENV if not os.getenv(key)]
if missing:
    raise RuntimeError(
        f"Critical environment variables missing: {', '.join(missing)}\n"
        f"Check your .env file."
    )

# Warn (don't crash) if Twilio is missing — SMS will be skipped
if not os.getenv("TWILIO_ACCOUNT_SID"):
    print("WARNING: TWILIO_ACCOUNT_SID not set — SMS confirmations will be skipped (dev mode)")

# ── Logging setup ─────────────────────────────────────────
class PIIFilter(logging.Filter):
    """Redact phone numbers from log output to protect patient privacy."""
    def filter(self, record):
        msg = str(record.msg)
        msg = re.sub(r'\+1\d{10}', '+1**MASKED**', msg)
        msg = re.sub(r'\+91\d{10}', '+91**MASKED**', msg)
        record.msg = msg
        return True

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
logger = logging.getLogger(__name__)
logger.addFilter(PIIFilter())

# ── App ───────────────────────────────────────────────────
app = FastAPI(
    title="MediVoice AI — Hospital Voice Booking System",
    description=(
        "AI-powered appointment booking via voice calls. "
        "Uses Vapi for conversational AI, Google Calendar for scheduling, "
        "Supabase for persistence, and Twilio for SMS confirmations."
    ),
    version="1.0.0",
    # Swagger UI only available in development
    docs_url="/docs" if os.getenv("ENVIRONMENT") == "development" else None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://your-dashboard.vercel.app",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────
app.include_router(vapi_webhook.router)
app.include_router(auth.router)
app.include_router(admin.router)

# ── Health check ──────────────────────────────────────────
@app.get("/health")
def health():
    """Health check endpoint for Railway / load balancer."""
    return {
        "status": "ok",
        "environment": os.getenv("ENVIRONMENT", "development"),
        "hospital": os.getenv("HOSPITAL_NAME", "City Hospital"),
        "timezone": os.getenv("TIMEZONE", "America/New_York"),
    }

# ── Global error handler ──────────────────────────────────
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception on {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"}
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8000)),
        reload=os.getenv("ENVIRONMENT") == "development"
    )
