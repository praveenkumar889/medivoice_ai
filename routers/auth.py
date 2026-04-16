import os
import json
import logging
from fastapi import APIRouter
from fastapi.responses import RedirectResponse, JSONResponse
from google_auth_oauthlib.flow import Flow
from services.supabase_service import save_gcal_tokens

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def get_flow() -> Flow:
    """Create a Google OAuth2 flow using client secrets from environment."""
    client_config = {
        "web": {
            "client_id": os.environ.get("GOOGLE_CLIENT_ID"),
            "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET"),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }
    return Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        redirect_uri=os.getenv("GOOGLE_REDIRECT_URI")
    )


@router.get("/google")
def google_login():
    """Step 1: Redirect to Google OAuth consent screen."""
    flow = get_flow()
    auth_url, _ = flow.authorization_url(
        prompt="consent",
        access_type="offline",
        include_granted_scopes="true"
    )
    return RedirectResponse(auth_url)


@router.get("/google/callback")
async def google_callback(code: str, error: str = None):
    """Step 2: Google redirects here after user grants access."""
    if error:
        logger.error(f"Google OAuth error: {error}")
        return JSONResponse(
            status_code=400,
            content={"error": f"OAuth failed: {error}"}
        )
    try:
        flow = get_flow()
        flow.fetch_token(code=code)
        creds = flow.credentials
        await save_gcal_tokens({
            "token":         creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri":     creds.token_uri,
            "client_id":     creds.client_id,
            "client_secret": creds.client_secret,
        })
        logger.info("Google Calendar OAuth completed successfully")
        return {"message": "Google Calendar connected. You can close this tab."}
    except Exception as e:
        logger.error(f"OAuth callback failed: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})
