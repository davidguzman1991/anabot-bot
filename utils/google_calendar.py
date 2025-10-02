from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/calendar"]
CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")
TOKEN_PATH = os.getenv("GOOGLE_CALENDAR_TOKEN", "token.json")
TOKEN_JSON = os.getenv("GOOGLE_CALENDAR_TOKEN_JSON")


def _load_credentials() -> Optional[Credentials]:
    if TOKEN_JSON:
        data = json.loads(TOKEN_JSON)
        return Credentials.from_authorized_user_info(data, scopes=SCOPES)
    if TOKEN_PATH and os.path.exists(TOKEN_PATH):
        return Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    return None


def create_calendar_event(
    summary: str,
    description: str,
    start_dt: datetime,
    duration_minutes: int,
    location: str = "",
):
    creds = _load_credentials()
    if not creds:
        return None

    service = build("calendar", "v3", credentials=creds)
    end_dt = start_dt + timedelta(minutes=duration_minutes)

    event = {
        "summary": summary,
        "description": description,
        "location": location,
        "start": {
            "dateTime": start_dt.isoformat(),
            "timeZone": "America/Guayaquil",
        },
        "end": {
            "dateTime": end_dt.isoformat(),
            "timeZone": "America/Guayaquil",
        },
    }

    created = service.events().insert(calendarId=CALENDAR_ID, body=event, sendUpdates="all").execute()
    return {"id": created.get("id"), "htmlLink": created.get("htmlLink")}
