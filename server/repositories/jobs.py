from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from server.models import Job


def utcnow():
    return datetime.now(timezone.utc)


def get_job(db: Session, job_id: str) -> Job | None:
    return db.get(Job, job_id)


def get_by_idempotency_key(db: Session, key: str | None) -> Job | None:
    if not key:
        return None
    return db.scalar(select(Job).where(Job.idempotency_key == key))


def claim_next_job(db: Session, worker_id: str, lease_seconds: int = 120) -> Job | None:
    now = utcnow()
    query = (
        select(Job)
        .where(
            or_(
                Job.status == "QUEUED",
                (Job.status == "RETRY_WAIT") & (Job.lease_expires_at <= now),
            )
        )
        .order_by(Job.priority.asc(), Job.created_at.asc())
        .limit(1)
    )
    if db.bind and db.bind.dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)
    candidate = db.scalar(query)
    if candidate is None:
        return None
    claimed = db.execute(
        update(Job)
        .where(Job.id == candidate.id, Job.status.in_(["QUEUED", "RETRY_WAIT"]))
        .values(
            status="RUNNING",
            lease_owner=worker_id,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
            heartbeat_at=now,
            started_at=Job.started_at if candidate.started_at else now,
            attempt=candidate.attempt + 1,
        )
    )
    if claimed.rowcount != 1:
        db.rollback()
        return None
    db.commit()
    return db.get(Job, candidate.id)


def reclaim_expired(db: Session) -> int:
    now = utcnow()
    result = db.execute(
        update(Job)
        .where(Job.status == "RUNNING", Job.lease_expires_at < now)
        .values(status="QUEUED", lease_owner=None, lease_expires_at=None, heartbeat_at=None)
    )
    db.commit()
    return result.rowcount
