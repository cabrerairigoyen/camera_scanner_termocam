import asyncio
import json

from fastapi import APIRouter, Depends, File, Form, Header, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from server.db import SessionLocal, get_db
from server.errors import api_error
from server.models import DocumentPage, Event, Job
from server.repositories.documents import get_document, ordered_pages
from server.schemas import DocumentCreate, DocumentFinish, PageMetadata, PageReorder, PageUpdate
from server.services import artifacts
from server.services.documents import add_page, create_document, finish_document, serialize_document
from server.services.ids import valid_id
from server.services.security import validate_upload


router = APIRouter(prefix="/documents", tags=["documents"])


def _document(db: Session, document_id: str):
    if not valid_id(document_id, "doc"):
        raise api_error(404, "JOB_CONFLICT", "Document not found.")
    document = get_document(db, document_id)
    if not document:
        raise api_error(404, "JOB_CONFLICT", "Document not found.")
    return document


@router.post("")
async def post_document(
    body: DocumentCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
):
    document = create_document(
        db,
        course=body.course,
        language=body.language,
        title=body.title,
        idempotency_key=idempotency_key,
    )
    db.commit()
    return {
        "document_id": document.id,
        "status": document.status,
        "next_page_number": serialize_document(db, document)["next_page_number"],
    }


@router.post("/{document_id}/pages")
async def post_page(
    document_id: str,
    file: UploadFile = File(...),
    metadata_json: str = Form(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
):
    document = _document(db, document_id)
    if idempotency_key:
        existing = db.scalar(
            select(Job).where(Job.idempotency_key == f"page:{document.id}:{idempotency_key}")
        )
        if existing and existing.page_id:
            page = db.get(DocumentPage, existing.page_id)
            return {
                "page_id": page.id,
                "status": page.status,
                "quality_job_id": existing.id,
                "events_url": f"/documents/{document.id}/events",
            }
    try:
        metadata = PageMetadata.model_validate_json(metadata_json)
    except Exception as exc:
        raise api_error(422, "INVALID_UPLOAD", "metadata_json is invalid.") from exc
    data = await file.read()
    content_type = validate_upload(data, file.filename or "", {"image", "zip"})
    kind = "source_zip" if content_type == "application/zip" else "source_image"
    artifact = artifacts.save_bytes(
        db,
        data,
        kind=kind,
        content_type=content_type,
        document_id=document.id,
        extension=".zip" if kind == "source_zip" else ".jpg",
    )
    try:
        page, job = add_page(
            db,
            document=document,
            page_number=metadata.page_number,
            source_artifact_id=artifact.id,
            replace_page_id=metadata.replace_page_id,
            idempotency_key=idempotency_key,
        )
        artifact.page_id = page.id
        artifact.job_id = job.id
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise api_error(409, "JOB_CONFLICT", str(exc)) from exc
    return {
        "page_id": page.id,
        "status": page.status,
        "quality_job_id": job.id,
        "events_url": f"/documents/{document.id}/events",
    }


@router.post("/{document_id}/finish")
async def finish(
    document_id: str,
    body: DocumentFinish,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
):
    document = _document(db, document_id)
    try:
        job = finish_document(
            db,
            document,
            expected_page_count=body.expected_page_count,
            solve=body.solve,
            answer_mode=body.answer_mode,
            allow_rejected=body.allow_rejected,
            idempotency_key=idempotency_key,
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise api_error(409, "JOB_CONFLICT", str(exc), recoverable=True, recommended_action="retake_page") from exc
    return {"document_id": document.id, "status": document.status, "job_id": job.id}


@router.get("/{document_id}")
async def get_document_route(document_id: str, db: Session = Depends(get_db)):
    return serialize_document(db, _document(db, document_id))


@router.get("/{document_id}/events")
async def events(document_id: str, after_sequence: int = 0, db: Session = Depends(get_db)):
    _document(db, document_id)
    rows = list(
        db.scalars(
            select(Event)
            .where(Event.document_id == document_id, Event.sequence > after_sequence)
            .order_by(Event.sequence.asc())
        )
    )
    values = [_serialize_event(row, db) for row in rows]
    return {"events": values, "next_after_sequence": values[-1]["sequence"] if values else after_sequence}


@router.get("/{document_id}/events/stream")
async def event_stream(document_id: str, after_sequence: int = 0):
    async def generate():
        cursor = after_sequence
        while True:
            with SessionLocal() as db:
                rows = list(
                    db.scalars(
                        select(Event)
                        .where(Event.document_id == document_id, Event.sequence > cursor)
                        .order_by(Event.sequence.asc())
                    )
                )
                for row in rows:
                    cursor = row.sequence
                    yield f"data: {json.dumps(_serialize_event(row, db))}\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(generate(), media_type="text/event-stream")


def _serialize_event(row: Event, db: Session) -> dict:
    page = db.get(DocumentPage, row.page_id) if row.page_id else None
    return {
        "event_id": row.id,
        "sequence": row.sequence,
        "event_type": row.event_type,
        "severity": row.severity,
        "message_key": row.message_key,
        "spoken_text": row.spoken_text,
        "document_id": row.document_id,
        "job_id": row.job_id,
        "page_number": page.page_number if page else None,
        "dedupe_key": row.dedupe_key,
        "created_at": row.created_at,
        "expires_at": row.expires_at,
        "payload": json.loads(row.payload_json or "{}"),
    }


@router.get("/{document_id}/debug_report.json")
async def document_debug(document_id: str, db: Session = Depends(get_db)):
    document = _document(db, document_id)
    return {
        "schema_version": "1.0",
        "document_id": document.id,
        "status": document.status,
        "version": document.version,
        "pages": serialize_document(db, document)["pages"],
        "errors": [],
    }


@router.put("/{document_id}/pages/{page_id}")
async def update_page(document_id: str, page_id: str, body: PageUpdate, db: Session = Depends(get_db)):
    document = _document(db, document_id)
    page = db.get(DocumentPage, page_id)
    if not page or page.document_id != document.id:
        raise api_error(404, "JOB_CONFLICT", "Page not found.")
    if body.page_number is not None and body.page_number != page.page_number:
        page.page_number = body.page_number
        document.version += 1
    db.commit()
    return serialize_document(db, document)


@router.delete("/{document_id}/pages/{page_id}")
async def delete_page(document_id: str, page_id: str, db: Session = Depends(get_db)):
    document = _document(db, document_id)
    page = db.get(DocumentPage, page_id)
    if not page or page.document_id != document.id:
        raise api_error(404, "JOB_CONFLICT", "Page not found.")
    db.delete(page)
    document.version += 1
    db.commit()
    return {"document_id": document.id, "deleted_page_id": page_id, "version": document.version}


@router.post("/{document_id}/pages/reorder")
async def reorder(document_id: str, body: PageReorder, db: Session = Depends(get_db)):
    document = _document(db, document_id)
    pages = ordered_pages(db, document.id)
    if set(body.page_ids) != {page.id for page in pages} or len(body.page_ids) != len(pages):
        raise api_error(409, "JOB_CONFLICT", "page_ids must contain every page exactly once.")
    by_id = {page.id: page for page in pages}
    for number, page_id in enumerate(body.page_ids, start=1):
        by_id[page_id].page_number = -number
    db.flush()
    for number, page_id in enumerate(body.page_ids, start=1):
        by_id[page_id].page_number = number
    document.version += 1
    db.commit()
    return serialize_document(db, document)
