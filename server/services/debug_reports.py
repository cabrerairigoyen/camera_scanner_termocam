import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.models import Artifact, Job, JobStep
from server.services.artifacts import save_json


def build_debug_report(db: Session, job: Job, quality: dict | None = None) -> dict:
    steps = list(
        db.scalars(select(JobStep).where(JobStep.job_id == job.id).order_by(JobStep.started_at.asc()))
    )
    artifact_rows = list(db.scalars(select(Artifact).where(Artifact.job_id == job.id)))
    errors = []
    if job.error_code:
        try:
            details = json.loads(job.error_json or "{}")
        except json.JSONDecodeError:
            details = {}
        errors.append({"code": job.error_code, **details})
    return {
        "schema_version": "1.0",
        "job_id": job.id,
        "document_id": job.document_id,
        "status": job.status,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "pipeline_steps": [
            {
                "name": step.name,
                "status": step.status,
                "attempt": step.attempt,
                "duration_ms": step.duration_ms,
                "warnings": json.loads(step.warnings_json or "[]"),
                "metrics": json.loads(step.metrics_json or "{}"),
            }
            for step in steps
        ],
        "quality": quality or {},
        "errors": errors,
        "artifacts": [{"kind": row.kind, "artifact_id": row.id} for row in artifact_rows],
    }


def save_debug_report(db: Session, job: Job, quality: dict | None = None):
    existing = list(
        db.scalars(
            select(Artifact).where(Artifact.job_id == job.id, Artifact.kind == "debug_report")
        )
    )
    for artifact in existing:
        db.delete(artifact)
    return save_json(
        db,
        build_debug_report(db, job, quality),
        kind="debug_report",
        job_id=job.id,
        document_id=job.document_id,
        page_id=job.page_id,
    )
