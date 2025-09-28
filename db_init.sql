-- AnaBot core schema
CREATE TABLE IF NOT EXISTS patients (
  dni TEXT PRIMARY KEY,
  full_name TEXT,
  birth_date TEXT,
  phone_ec TEXT,
  email TEXT,
  wa_user_id TEXT,
  tg_user_id TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS appointments (
  id BIGSERIAL PRIMARY KEY,
  patient_dni TEXT REFERENCES patients(dni) ON DELETE CASCADE,
  site TEXT CHECK (site IN ('GYE','MIL')) NOT NULL,
  starts_at TIMESTAMPTZ NOT NULL,
  status TEXT CHECK (status IN ('PENDING','CONFIRMED','CANCELLED')) DEFAULT 'PENDING',
  reminder_channel TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sessions (
  channel TEXT CHECK (channel IN ('wa','tg')) NOT NULL,
  user_key TEXT NOT NULL,
  state JSONB NOT NULL DEFAULT '{}'::jsonb,
  updated_at TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (channel, user_key)
);
CREATE TABLE IF NOT EXISTS contact_requests (
  id BIGSERIAL PRIMARY KEY,
  platform TEXT,
  user_key TEXT,
  raw_text TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);
