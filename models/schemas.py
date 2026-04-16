from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class TimeSlot(BaseModel):
    """Represents a single available appointment slot."""
    start: str            # "10:30 AM"
    start_iso: str        # "2026-04-20T10:30:00+05:30"
    end_iso: str          # "2026-04-20T11:00:00+05:30"


class Department(BaseModel):
    """Hospital department with its linked Google Calendar."""
    id: str
    name: str
    gcal_id: str
    is_active: bool


class Patient(BaseModel):
    """Patient record."""
    id: str
    name: Optional[str]
    phone: str
    created_at: datetime


class BookingCreate(BaseModel):
    """Payload for creating a new appointment booking."""
    patient_id: str
    dept_id: str
    doctor_id: Optional[str] = None
    start_time: str
    end_time: str
    gcal_event_id: str
    channel: str = "voice"


class Booking(BaseModel):
    """Full booking record from the database."""
    id: str
    patient_id: str
    dept_id: str
    start_time: datetime
    end_time: datetime
    status: str
    gcal_event_id: Optional[str]
    channel: str
    created_at: datetime


class VapiFunctionCallMessage(BaseModel):
    """Represents a function call from Vapi during a conversation."""
    name: str
    parameters: dict


class CheckAvailabilityParams(BaseModel):
    department: str
    date: str


class CreateBookingParams(BaseModel):
    department: str
    date: str
    chosen_slot: str
    patient_name: str
    patient_phone: str = Field(pattern=r"^\+?\d{10,15}$")


class VapiEndOfCallMessage(BaseModel):
    """Represents the end-of-call report from Vapi."""
    recordingUrl: Optional[str]
    durationSeconds: Optional[int]
