import io
import json
import zipfile
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import inspect, select

from server.db import Base
from server.errors import error_payload
from server.models import Artifact, Document, DocumentPage, Event, Job
from server.repositories.jobs import claim_next_job, reclaim_expired
from server.services import artifacts
from server.services.documents import create_document
from server.services.events import emit_event
from server.services.ids import new_id
from server.services.jobs import create_job
from server.services.security import validate_zip_bytes
from server.services.worker import RecoverableJobError, WorkerService


def test_tables_created(session_factory):
    names = set(inspect(session_factory.kw["bind"]).get_table_names())
    assert {
        "documents", "document_pages", "jobs", "job_steps", "artifacts",
        "events", "questions", "answers",
    }.issubset(names)


def test_create_document_and_idempotency(client):
    headers = {"Idempotency-Key": "create-exam-1"}
    first = client.post("/documents", json={"course": "GFN252", "language": "fr"}, headers=headers)
    second = client.post("/documents", json={"course": "ignored"}, headers=headers)
    assert first.status_code == 200
    assert first.json()["document_id"] == second.json()["document_id"]
    assert first.json()["status"] == "CAPTURING"


def test_upload_page_and_idempotency(client, png_bytes):
    document_id = client.post("/documents", json={}).json()["document_id"]
    files = {
        "file": ("page.png", png_bytes, "image/png"),
        "metadata_json": (None, json.dumps({"page_number": 1, "capture_mode": "still"})),
    }
    headers = {"Idempotency-Key": "page-one"}
    first = client.post(f"/documents/{document_id}/pages", files=files, headers=headers)
    second = client.post(f"/documents/{document_id}/pages", files=files, headers=headers)
    assert first.status_code == 200
    assert first.json()["status"] == "QUALITY_CHECK_PENDING"
    assert first.json()["page_id"] == second.json()["page_id"]
    assert first.json()["quality_job_id"] == second.json()["quality_job_id"]


def test_create_and_claim_job(db):
    job = create_job(db, job_type="PROCESS_STILL")
    db.commit()
    claimed = claim_next_job(db, "worker-1", lease_seconds=30)
    assert claimed.id == job.id
    assert claimed.status == "RUNNING"
    assert claimed.lease_owner == "worker-1"
    assert claimed.attempt == 1


def test_worker_success(session_factory):
    with session_factory() as db:
        job = create_job(db, job_type="PROCESS_STILL")
        db.commit()
        job_id = job.id
    worker = WorkerService(session_factory)
    worker.handlers["PROCESS_STILL"] = lambda db, job: None
    assert worker.run_once() is True
    with session_factory() as db:
        completed = db.get(Job, job_id)
        assert completed.status == "SUCCEEDED"
        assert artifacts.latest_artifact(db, job_id, "debug_report") is not None


def test_worker_failure_retry(session_factory):
    with session_factory() as db:
        job = create_job(db, job_type="PROCESS_STILL", max_attempts=2)
        db.commit()
        job_id = job.id
    worker = WorkerService(session_factory)

    def fail(_db, _job):
        raise RecoverableJobError("STORAGE_UNAVAILABLE", "temporary")

    worker.handlers["PROCESS_STILL"] = fail
    worker.run_once()
    with session_factory() as db:
        failed = db.get(Job, job_id)
        assert failed.status == "RETRY_WAIT"
        assert failed.error_code == "STORAGE_UNAVAILABLE"


def test_expired_lease_recovery(db):
    job = create_job(db, job_type="PROCESS_STILL")
    job.status = "RUNNING"
    job.lease_owner = "dead-worker"
    job.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    assert reclaim_expired(db) == 1
    assert db.get(Job, job.id).status == "QUEUED"


def test_job_cancellation(client):
    created = client.post("/jobs", json={"type": "PROCESS_STILL"}).json()
    response = client.post(f"/jobs/{created['job_id']}/cancel", json={"reason": "operator"})
    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLED"


def test_artifact_safe_path_and_hash(db, tmp_path):
    artifact = artifacts.save_bytes(
        db,
        b"hello",
        kind="ocr_json",
        content_type="application/json",
        extension=".json",
    )
    db.commit()
    assert artifacts.artifact_path(artifact).read_bytes() == b"hello"
    assert artifact.byte_size == 5
    assert len(artifact.sha256) == 64
    escaped = Artifact(
        id=new_id("art"),
        kind="ocr_json",
        storage_key="../escape",
        content_type="application/json",
        byte_size=0,
        sha256="0" * 64,
    )
    with pytest.raises(artifacts.ArtifactStorageError):
        artifacts.artifact_path(escaped)


def test_zip_path_traversal_rejected():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("../escape.txt", "bad")
    with pytest.raises(Exception) as exc:
        validate_zip_bytes(output.getvalue())
    assert getattr(exc.value, "detail", {}).get("error", {}).get("code") == "ZIP_UNSAFE"


def test_stable_job_shape(client):
    created = client.post("/jobs", json={"type": "PROCESS_STILL"}).json()
    response = client.get(f"/jobs/{created['job_id']}")
    assert set(response.json()) == {
        "job_id", "document_id", "page_id", "type", "status", "progress",
        "current_step", "attempt", "error", "result_urls",
    }


def _document_with_page(db, status="OCR_DONE", accepted=True, page_number=1, text="page"):
    document = create_document(db, course=None, language="fr", title=None)
    page = DocumentPage(
        id=new_id("page"),
        document_id=document.id,
        page_number=page_number,
        status=status,
        accepted=accepted,
        ocr_text=text,
        metrics_json=json.dumps({"ocr_confidence": 0.9}),
        warnings_json="[]",
    )
    db.add(page)
    db.flush()
    ocr = artifacts.save_json(
        db,
        {"text": text, "lines": []},
        kind="ocr_json",
        document_id=document.id,
        page_id=page.id,
    )
    page.ocr_artifact_id = ocr.id
    return document, page


def test_document_finish_with_accepted_page(client, session_factory):
    with session_factory() as db:
        document, _page = _document_with_page(db)
        document_id = document.id
        db.commit()
    response = client.post(
        f"/documents/{document_id}/finish",
        json={"expected_page_count": 1, "solve": False, "answer_mode": "standard"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "PROCESSING"


def test_document_finish_rejects_bad_page(client, session_factory):
    with session_factory() as db:
        document, _page = _document_with_page(db, status="REJECTED", accepted=False)
        document_id = document.id
        db.commit()
    response = client.post(
        f"/documents/{document_id}/finish",
        json={"expected_page_count": 1, "solve": False},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "JOB_CONFLICT"


def test_multi_page_ocr_merge_order(session_factory):
    with session_factory() as db:
        document = create_document(db, course=None, language="fr", title=None)
        for page_number, text in ((2, "second"), (1, "first")):
            page = DocumentPage(
                id=new_id("page"),
                document_id=document.id,
                page_number=page_number,
                status="OCR_DONE",
                accepted=True,
                ocr_text=text,
                metrics_json=json.dumps({"ocr_confidence": 0.9}),
                warnings_json="[]",
            )
            db.add(page)
            db.flush()
            ocr = artifacts.save_json(
                db,
                {"text": text, "lines": []},
                kind="ocr_json",
                document_id=document.id,
                page_id=page.id,
            )
            page.ocr_artifact_id = ocr.id
        job = create_job(
            db,
            job_type="DOCUMENT_FINALIZE",
            document_id=document.id,
            payload={"solve": False},
        )
        db.commit()
        job_id = job.id
    worker = WorkerService(session_factory)
    worker.run_once()
    with session_factory() as db:
        artifact = artifacts.latest_artifact(db, job_id, "ocr_json")
        merged = json.loads(artifacts.artifact_path(artifact).read_text())
        assert [page["page_number"] for page in merged["pages"]] == [1, 2]
        assert merged["text"] == "first\n\nsecond"


def test_event_order_and_polling(client, session_factory):
    with session_factory() as db:
        document = create_document(db, course=None, language=None, title=None)
        for key in ("one", "two", "three"):
            emit_event(
                db,
                document_id=document.id,
                event_type="SYSTEM",
                severity="info",
                message_key=key,
            )
        document_id = document.id
        db.commit()
    response = client.get(f"/documents/{document_id}/events?after_sequence=1")
    assert [event["sequence"] for event in response.json()["events"]] == [2, 3]
    assert response.json()["next_after_sequence"] == 3


def test_solver_unavailable_is_recoverable(session_factory, monkeypatch):
    monkeypatch.delenv("SOLVER_BASE_URL", raising=False)
    with session_factory() as db:
        document, _page = _document_with_page(db)
        job = create_job(
            db,
            job_type="SOLVER_DISPATCH",
            document_id=document.id,
            payload={"answer_mode": "standard", "document_version": 1},
        )
        db.commit()
        job_id = job.id
    WorkerService(session_factory).run_once()
    with session_factory() as db:
        job = db.get(Job, job_id)
        assert job.status == "RETRY_WAIT"
        assert job.error_code == "SOLVER_UNAVAILABLE"


def test_shared_error_schema():
    payload = error_payload("OCR_LOW_CONFIDENCE", "Low confidence", recoverable=True)
    assert payload["status"] == "FAILED"
    assert payload["error"]["code"] == "OCR_LOW_CONFIDENCE"
    assert payload["error"]["recoverable"] is True
    assert payload["error"]["trace_id"].startswith("trace_")
