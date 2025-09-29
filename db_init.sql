CREATE TABLE IF NOT EXISTS conversation_logs(
  id SERIAL PRIMARY KEY,
  user_id TEXT NOT NULL,
  message TEXT,
  response TEXT,
  platform TEXT,
  created_at TIMESTAMP DEFAULT NOW(),
  handoff BOOLEAN DEFAULT FALSE,
  status TEXT DEFAULT ''pendiente''
);

CREATE INDEX IF NOT EXISTS idx_convlogs_user_time ON conversation_logs(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS appointments(
  id SERIAL PRIMARY KEY,
  patient_dni TEXT,
  site TEXT,
  starts_at TIMESTAMPTZ,
  status TEXT DEFAULT ''PENDING'',
  reminder_channel TEXT,
  user_id TEXT,
  appointment_date TIMESTAMP,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_appts_time ON appointments(appointment_date DESC);
CREATE INDEX IF NOT EXISTS idx_appts_start ON appointments(starts_at DESC);

CREATE TABLE IF NOT EXISTS patients(
  id SERIAL PRIMARY KEY,
  user_id TEXT UNIQUE,
  name TEXT,
  dni TEXT UNIQUE,
  phone TEXT,
  full_name TEXT,
  birth_date TEXT,
  phone_ec TEXT,
  email TEXT,
  wa_user_id TEXT,
  tg_user_id TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sessions (
  channel TEXT CHECK (channel IN (''wa'',''tg'')) NOT NULL,
  user_key TEXT NOT NULL,
  state JSONB NOT NULL DEFAULT ''{}''::jsonb,
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
