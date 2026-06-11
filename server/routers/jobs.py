import json
import re
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from server.db import get_db
from server.errors import api_error
from server.models import Answer, Job, Question
from server.schemas import JobCancel, JobCreate, JobRetry
from server.services import artifacts
from server.services.ids import valid_id
from server.services.jobs import create_job, request_cancel, retry_job, serialize_job


router = APIRouter(prefix="/jobs", tags=["jobs"])
LEGACY_JOB_PATTERN = re.compile(r"^job(?:_still)?_[0-9]+_[0-9a-f]{6}$")
LEGACY_JOBS_ROOT = Path(__file__).resolve().parents[1] / "data" / "jobs"

RESULTS = {
    "reconstructed.jpg": ("reconstructed_jpg", "image/jpeg"),
    "reconstructed.pdf": ("reconstructed_pdf", "application/pdf"),
    "ocr.json": ("ocr_json", "application/json"),
    "debug_report.json": ("debug_report", "application/json"),
    "answers.json": ("answers_json", "application/json"),
}


def _get_job(db: Session, job_id: str) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise api_error(404, "JOB_CONFLICT", "Job not found.")
    return job


@router.post("")
async def post_job(body: JobCreate, db: Session = Depends(get_db)):
    try:
        job = create_job(
            db,
            job_type=body.type,
            document_id=body.document_id,
            page_id=body.page_id,
            payload=body.payload,
            priority=body.priority,
            max_attempts=body.max_attempts,
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise api_error(422, "JOB_CONFLICT", str(exc)) from exc
    return serialize_job(db, job)


@router.get("/{job_id}")
async def get_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job:
        return serialize_job(db, job)
    if LEGACY_JOB_PATTERN.fullmatch(job_id):
        legacy_dir = LEGACY_JOBS_ROOT / job_id
        report = legacy_dir / "debug_report.json"
        if legacy_dir.is_dir():
            status = "SUCCEEDED" if report.exists() else "FAILED"
            return {
                "job_id": job_id,
                "document_id": None,
                "page_id": None,
                "type": "PROCESS_SWEEP",
                "status": status,
                "progress": 1.0,
                "current_step": None,
                "attempt": 1,
                "error": None,
                "result_urls": {
                    "reconstructed_jpg": f"/jobs/{job_id}/result/reconstructed.jpg" if (legacy_dir / "reconstructed.jpg").exists() else None,
                    "reconstructed_pdf": f"/jobs/{job_id}/result/reconstructed.pdf" if (legacy_dir / "reconstructed.pdf").exists() else None,
                    "ocr_json": f"/jobs/{job_id}/result/ocr.json" if (legacy_dir / "ocr.json").exists() else None,
                    "debug_report": f"/jobs/{job_id}/result/debug_report.json" if report.exists() else None,
                    "answers_json": None,
                },
            }
    raise api_error(404, "JOB_CONFLICT", "Job not found.")


@router.post("/{job_id}/retry")
async def retry(job_id: str, body: JobRetry, db: Session = Depends(get_db)):
    job = _get_job(db, job_id)
    try:
        retry_job(job, body.reason)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise api_error(409, "JOB_NOT_RETRYABLE", str(exc)) from exc
    return serialize_job(db, job)


@router.post("/{job_id}/cancel")
async def cancel(job_id: str, body: JobCancel, db: Session = Depends(get_db)):
    job = _get_job(db, job_id)
    request_cancel(job, body.reason)
    db.commit()
    return serialize_job(db, job)


def _result_file(db: Session, job_id: str, filename: str):
    kind, media_type = RESULTS[filename]
    artifact = artifacts.latest_artifact(db, job_id, kind)
    if artifact:
        return FileResponse(artifacts.artifact_path(artifact), media_type=media_type)
    if LEGACY_JOB_PATTERN.fullmatch(job_id):
        path = LEGACY_JOBS_ROOT / job_id / filename
        if path.is_file():
            return FileResponse(path, media_type=media_type)
    raise api_error(404, "JOB_CONFLICT", "Result is not available.")


@router.get("/{job_id}/result/reconstructed.jpg")
async def reconstructed_jpg(job_id: str, db: Session = Depends(get_db)):
    return _result_file(db, job_id, "reconstructed.jpg")


@router.get("/{job_id}/result/reconstructed.pdf")
async def reconstructed_pdf(job_id: str, db: Session = Depends(get_db)):
    return _result_file(db, job_id, "reconstructed.pdf")


@router.get("/{job_id}/result/ocr.json")
async def ocr_json(job_id: str, db: Session = Depends(get_db)):
    return _result_file(db, job_id, "ocr.json")


@router.get("/{job_id}/result/debug_report.json")
async def debug_report(job_id: str, db: Session = Depends(get_db)):
    return _result_file(db, job_id, "debug_report.json")


@router.get("/{job_id}/result/answers.json")
async def answers(job_id: str, db: Session = Depends(get_db)):
    return _result_file(db, job_id, "answers.json")


@router.get("/{job_id}/result/audio/{question_id}.mp3")
async def audio(job_id: str, question_id: str, db: Session = Depends(get_db)):
    job = _get_job(db, job_id)
    question = db.scalar(
        select(Question).where(
            Question.document_id == job.document_id,
            Question.stable_question_id == question_id,
        )
    )
    answer = None
    if question:
        answer = db.scalar(
            select(Answer)
            .where(Answer.question_id == question.id)
            .order_by(Answer.created_at.desc())
        )
    artifact = db.get(artifacts.Artifact, answer.audio_artifact_id) if answer and answer.audio_artifact_id else None
    if artifact:
        return FileResponse(artifacts.artifact_path(artifact), media_type="audio/mpeg")
    raise api_error(404, "TTS_FAILED", "Audio result is not available.", recoverable=True)
