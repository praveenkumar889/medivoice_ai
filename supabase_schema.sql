-- ================================================================
-- MediVoice AI — Supabase Schema
-- Run this in: Supabase → SQL Editor → New Query → Run
-- ================================================================

-- Google Calendar OAuth tokens (one row per system)
CREATE TABLE IF NOT EXISTS gcal_tokens (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  token_json TEXT        NOT NULL,
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- Hospital departments (each linked to a Google Calendar)
CREATE TABLE IF NOT EXISTS departments (
  id         UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
  name       TEXT    NOT NULL,
  gcal_id    TEXT    NOT NULL,
  is_active  BOOLEAN DEFAULT true
);

-- Doctors per department
CREATE TABLE IF NOT EXISTS doctors (
  id        UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
  dept_id   UUID    REFERENCES departments(id),
  name      TEXT    NOT NULL,
  gcal_id   TEXT    NOT NULL,
  is_active BOOLEAN DEFAULT true
);

-- Patients (identified by phone number)
CREATE TABLE IF NOT EXISTS patients (
  id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  name       TEXT,
  phone      TEXT        UNIQUE NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- Appointment bookings
CREATE TABLE IF NOT EXISTS bookings (
  id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id    UUID        REFERENCES patients(id),
  doctor_id     UUID        REFERENCES doctors(id),
  dept_id       UUID        REFERENCES departments(id),
  start_time    TIMESTAMPTZ NOT NULL,
  end_time      TIMESTAMPTZ NOT NULL,
  status        TEXT        DEFAULT 'confirmed',
  gcal_event_id TEXT,
  channel       TEXT        DEFAULT 'voice',
  created_at    TIMESTAMPTZ DEFAULT now()
);

-- Call recordings + Whisper transcripts
CREATE TABLE IF NOT EXISTS interactions (
  id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id    UUID        REFERENCES patients(id),
  booking_id    UUID        REFERENCES bookings(id),
  recording_url TEXT,
  transcript    JSONB,
  duration_secs INT,
  created_at    TIMESTAMPTZ DEFAULT now()
);

-- SMS reminders (24h and 1h before each appointment)
CREATE TABLE IF NOT EXISTS reminders (
  id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  booking_id   UUID        REFERENCES bookings(id),
  type         TEXT,
  scheduled_at TIMESTAMPTZ,
  sent_at      TIMESTAMPTZ,
  status       TEXT        DEFAULT 'pending'
);

-- ── Indexes for performance ──────────────────────────────
CREATE INDEX IF NOT EXISTS idx_patients_phone      ON patients(phone);
CREATE INDEX IF NOT EXISTS idx_bookings_start_time ON bookings(start_time);
CREATE INDEX IF NOT EXISTS idx_bookings_dept_start ON bookings(dept_id, start_time);
CREATE INDEX IF NOT EXISTS idx_bookings_status     ON bookings(status);
CREATE INDEX IF NOT EXISTS idx_reminders_scheduled ON reminders(scheduled_at, status);
CREATE INDEX IF NOT EXISTS idx_interactions_patient ON interactions(patient_id);
