import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.models import Artifact, Job
from server.repositories.jobs import get_by_idempotency_key
from server.services.ids import new_id


JOB_TYPES = {
    "PROCESS_STILL",
    "PROCESS_SWEEP",
    "PAGE_QUALITY",
    "PAGE_OCR",
    "DOCUMENT_FINALIZE",
    "SOLVER_DISPATCH",
    "TTS_GENERATE",
}
TERMINAL_STATUSES = {"SUCCEEDED", "FAILED", "CANCELLED"}


def create_job(
    db: Session,
    *,
    job_type: str,
    document_id: str | None = None,
    page_id: str | None = None,
    payload: dict | None = None,
    priority: int = 100,
    max_attempts: int = 3,
    idempotency_key: str | None = None,
    job_id: str | None = None,
) -> Job:
    if job_type not in JOB_TYPES:
        raise ValueError(f"Unsupported job type: {job_type}")
    existing = get_by_idempotency_key(db, idempotency_key)
    if existing:
        return existing
    job = Job(
        id=job_id or new_id("job"),
        document_id=document_id,
        page_id=page_id,
        type=job_type,
        status="QUEUED",
        priority=priority,
        max_attempts=max_attempts,
        idempotency_key=idempotency_key,
        result_json=json.dumps(payload or {}),
    )
    db.add(job)
    db.flush()
    return job


def job_payload(job: Job) -> dict:
    try:
        return json.loads(job.result_json or "{}")
    except json.JSONDecodeError:
        return {}


def set_job_payload(job: Job, payload: dict) -> None:
    job.result_json = json.dumps(payload)


def result_urls(db: Session, job: Job) -> dict:
    kinds = {row.kind for row in db.scalars(select(Artifact).where(Artifact.job_id == job.id))}
    return {
        "reconstructed_jpg": f"/jobs/{job.id}/result/reconstructed.jpg" if "reconstructed_jpg" in kinds else None,
        "reconstructed_pdf": f"/jobs/{job.id}/result/reconstructed.pdf" if "reconstructed_pdf" in kinds else None,
        "ocr_json": f"/jobs/{job.id}/result/ocr.json" if "ocr_json" in kinds else None,
        "debug_report": f"/jobs/{job.id}/result/debug_report.json" if "debug_report" in kinds else None,
        "answers_json": f"/jobs/{job.id}/result/answers.json" if "answers_json" in kinds else None,
    }


def serialize_job(db: Session, job: Job) -> dict:
    error = None
    if job.error_code:
        try:
            error = json.loads(job.error_json or "{}")
        except json.JSONDecodeError:
            error = {"code": job.error_code, "message": "Job failed."}
    return {
        "job_id": job.id,
        "document_id": job.document_id,
        "page_id": job.page_id,
        "type": job.type,
        "status": job.status,
        "progress": job.progress,
        "current_step": job.current_step,
        "attempt": job.attempt,
        "error": error,
        "result_urls": result_urls(db, job),
    }


def request_cancel(job: Job, reason: str | None = None) -> Job:
    if job.status in TERMINAL_STATUSES:
        return job
    if job.status in {"QUEUED", "RETRY_WAIT"}:
        job.status = "CANCELLED"
        job.finished_at = datetime.now(timezone.utc)
        job.error_code = "JOB_CANCELLED"
    else:
        job.status = "CANCEL_REQUESTED"
    job.error_json = json.dumps({"reason": reason}) if reason else None
    return job


def retry_job(job: Job, reason: str | None = None) -> Job:
    if job.status not in {"FAILED", "CANCELLED"}:
        raise ValueError("Job is not retryable")
    job.status = "QUEUED"
    job.error_code = None
    job.error_json = None
    job.finished_at = None
    job.lease_owner = None
    job.lease_expires_at = None
    job.current_step = None
    job.progress = 0.0
    if reason:
        payload = job_payload(job)
        payload["retry_reason"] = reason
        set_job_payload(job, payload)
    return job
