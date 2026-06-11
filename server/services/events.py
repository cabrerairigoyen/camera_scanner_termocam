import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from server.models import Event
from server.repositories.documents import next_event_sequence
from server.services.ids import new_id


def emit_event(
    db: Session,
    *,
    document_id: str,
    event_type: str,
    severity: str,
    message_key: str,
    spoken_text: str | None = None,
    job_id: str | None = None,
    page_id: str | None = None,
    dedupe_key: str | None = None,
    payload: dict | None = None,
    cooldown_seconds: int = 0,
) -> Event:
    if dedupe_key:
        existing = db.scalar(select(Event).where(Event.dedupe_key == dedupe_key))
        if existing:
            return existing
        if cooldown_seconds:
            since = datetime.now(timezone.utc) - timedelta(seconds=cooldown_seconds)
            existing = db.scalar(
                select(Event).where(
                    Event.document_id == document_id,
                    Event.message_key == message_key,
                    Event.created_at >= since,
                )
            )
            if existing:
                return existing
    event = Event(
        id=new_id("evt"),
        document_id=document_id,
        job_id=job_id,
        page_id=page_id,
        sequence=next_event_sequence(db, document_id),
        event_type=event_type,
        severity=severity,
        message_key=message_key,
        spoken_text=spoken_text,
        dedupe_key=dedupe_key,
        payload_json=json.dumps(payload or {}),
    )
    db.add(event)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        if dedupe_key:
            existing = db.scalar(select(Event).where(Event.dedupe_key == dedupe_key))
            if existing:
                return existing
        raise
    return event
