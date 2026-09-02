from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class NotificationResponse(BaseModel):
    notification_id: str
    type: str
    title: str
    body: str
    read_at: datetime | None
    created_at: datetime
