import base64
import json
import uuid
from datetime import datetime
from typing import TypedDict


class UserCursor(TypedDict):
    created_at: str  # ISO format
    id: str  # UUID string


def encode_cursor(created_at: datetime, id_: uuid.UUID) -> str:
    payload = json.dumps({"created_at": created_at.isoformat(), "id": str(id_)})
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> UserCursor:
    # Re-pad for base64 decoder
    padded = cursor + "=" * (-len(cursor) % 4)
    raw = base64.urlsafe_b64decode(padded.encode()).decode()
    return json.loads(raw)
