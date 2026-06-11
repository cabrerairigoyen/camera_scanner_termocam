import json

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from server.db import get_db
from server.errors import api_error
from server.models import Answer, Job, Question
from server.services import artifacts
from server.services.events import emit_event
from server.services.ids import new_id
from server.services.security import require_solver_token


router = APIRouter(prefix="/solver-callbacks", tags=["solver"])


@router.post("/{job_id}", dependencies=[Depends(require_solver_token)])
async def solver_callback(job_id: str, body: dict, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job or job.type != "SOLVER_DISPATCH":
        raise api_error(404, "JOB_CONFLICT", "Solver job not found.")
    if body.get("status") != "DONE":
        job.status = "FAILED"
        job.error_code = "SOLVER_UNAVAILABLE"
        job.error_json = json.dumps(body)
        db.commit()
        return {"status": "FAILED"}
    solver_job_id = body.get("solver_job_id", "")
    for item in body.get("answers", []):
        question = db.scalar(
            select(Question).where(
                Question.document_id == job.document_id,
                Question.stable_question_id == item.get("question_id"),
            )
        )
        if not question:
            continue
        db.add(
            Answer(
                id=new_id("ans"),
                question_id=question.id,
                solver_job_id=solver_job_id,
                answer_text=item.get("answer_text"),
                selected_choices_json=json.dumps(item.get("selected_choices")),
                confidence=item.get("confidence"),
                citations_json=json.dumps(item.get("citations", [])),
                audio_artifact_id=item.get("audio_artifact_id"),
                model_json=json.dumps(body.get("model", {})),
            )
        )
    artifact = artifacts.save_json(
        db,
        body,
        kind="answers_json",
        job_id=job.id,
        document_id=job.document_id,
    )
    job.status = "SUCCEEDED"
    job.progress = 1.0
    emit_event(
        db,
        document_id=job.document_id,
        job_id=job.id,
        event_type="AUDIO_FEEDBACK",
        severity="success",
        message_key="answers_ready",
        spoken_text="Answers ready.",
        dedupe_key=f"{job.id}:answers-ready",
    )
    db.commit()
    return {"status": "SUCCEEDED", "answers_artifact_id": artifact.id}
