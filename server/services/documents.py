import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.models import Document, DocumentPage, Job
from server.repositories.documents import next_page_number, ordered_pages
from server.services.events import emit_event
from server.services.ids import new_id
from server.services.jobs import create_job


def create_document(
    db: Session,
    *,
    course: str | None,
    language: str | None,
    title: str | None,
    idempotency_key: str | None = None,
) -> Document:
    if idempotency_key:
        existing = db.scalar(select(Document).where(Document.idempotency_key == idempotency_key))
        if existing:
            return existing
    document = Document(
        id=new_id("doc"),
        status="CAPTURING",
        course=course,
        language=language,
        title=title,
        idempotency_key=idempotency_key,
    )
    db.add(document)
    db.flush()
    return document


def add_page(
    db: Session,
    *,
    document: Document,
    page_number: int | None,
    source_artifact_id: str,
    replace_page_id: str | None = None,
    idempotency_key: str | None = None,
) -> tuple[DocumentPage, Job]:
    if document.status != "CAPTURING":
        raise ValueError("Document is no longer accepting pages")
    if idempotency_key:
        existing = db.scalar(select(Job).where(Job.idempotency_key == f"page:{document.id}:{idempotency_key}"))
        if existing and existing.page_id:
            return db.get(DocumentPage, existing.page_id), existing
    if replace_page_id:
        page = db.get(DocumentPage, replace_page_id)
        if page is None or page.document_id != document.id:
            raise ValueError("Replacement page not found")
        page.source_artifact_id = source_artifact_id
        page.status = "QUALITY_CHECK_PENDING"
        page.accepted = False
        page.rejection_reason = None
        page.ocr_artifact_id = None
        page.ocr_text = None
        if page_number:
            page.page_number = page_number
        document.version += 1
    else:
        page = DocumentPage(
            id=new_id("page"),
            document_id=document.id,
            page_number=page_number or next_page_number(db, document.id),
            status="QUALITY_CHECK_PENDING",
            source_artifact_id=source_artifact_id,
        )
        db.add(page)
        db.flush()
    job = create_job(
        db,
        job_type="PAGE_QUALITY",
        document_id=document.id,
        page_id=page.id,
        payload={"source_artifact_id": source_artifact_id},
        idempotency_key=f"page:{document.id}:{idempotency_key}" if idempotency_key else None,
    )
    emit_event(
        db,
        document_id=document.id,
        page_id=page.id,
        job_id=job.id,
        event_type="PAGE_STATUS",
        severity="info",
        message_key="processing_started",
        spoken_text=f"Page {page.page_number} uploaded.",
        dedupe_key=f"{page.id}:uploaded:{document.version}",
        payload={"page_number": page.page_number},
    )
    return page, job


def finish_document(
    db: Session,
    document: Document,
    *,
    expected_page_count: int,
    solve: bool,
    answer_mode: str,
    allow_rejected: bool,
    idempotency_key: str | None,
) -> Job:
    pages = ordered_pages(db, document.id)
    if len(pages) != expected_page_count:
        raise ValueError("Page count does not match")
    invalid = [page for page in pages if page.status not in {"ACCEPTED", "OCR_DONE"}]
    if invalid and not allow_rejected:
        raise ValueError("Document contains unfinished or rejected pages")
    job = create_job(
        db,
        job_type="DOCUMENT_FINALIZE",
        document_id=document.id,
        payload={"solve": solve, "answer_mode": answer_mode, "document_version": document.version},
        idempotency_key=f"finish:{document.id}:{idempotency_key}" if idempotency_key else None,
    )
    document.status = "PROCESSING"
    document.active_job_id = job.id
    emit_event(
        db,
        document_id=document.id,
        job_id=job.id,
        event_type="AUDIO_FEEDBACK",
        severity="info",
        message_key="processing_started",
        spoken_text="Processing document.",
        dedupe_key=f"{document.id}:processing:{document.version}",
    )
    return job


def serialize_document(db: Session, document: Document) -> dict:
    pages = ordered_pages(db, document.id)
    return {
        "document_id": document.id,
        "status": document.status,
        "course": document.course,
        "language": document.language,
        "title": document.title,
        "version": document.version,
        "next_page_number": next_page_number(db, document.id),
        "active_job_id": document.active_job_id,
        "pages": [
            {
                "page_id": page.id,
                "page_number": page.page_number,
                "status": page.status,
                "accepted": page.accepted,
                "quality_score": page.quality_score,
                "rejection_reason": page.rejection_reason,
                "metrics": json.loads(page.metrics_json or "{}"),
                "warnings": json.loads(page.warnings_json or "[]"),
            }
            for page in pages
        ],
    }
