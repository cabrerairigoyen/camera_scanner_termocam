import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from server.db import get_db, init_db
from server.errors import api_error, error_payload
from server.models import Document, DocumentPage
from server.routers import documents, health, jobs, solver_callbacks
from server.services import artifacts
from server.services.documents import create_document
from server.services.ids import new_id
from server.services.jobs import create_job
from server.services.security import require_service_token, validate_upload


load_dotenv(Path(__file__).resolve().parents[1] / ".env")
STARTED_AT = time.monotonic()


def _cors_origins() -> list[str]:
    value = os.getenv("CORS_ALLOW_ORIGINS", "http://localhost,http://127.0.0.1")
    return [origin.strip() for origin in value.split(",") if origin.strip()]


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="TermoCam Reconstruction Server", version="2.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
)

app.include_router(health.router)
app.include_router(documents.router, dependencies=[Depends(require_service_token)])
app.include_router(jobs.router, dependencies=[Depends(require_service_token)])
app.include_router(solver_callbacks.router)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex}"
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    logging.getLogger("termocam.api").info(
        "request complete",
        extra={"request_id": request_id, "method": request.method, "path": request.url.path},
    )
    return response


@app.exception_handler(Exception)
async def unhandled_error(_request: Request, exc: Exception):
    logging.getLogger("termocam.api").exception("unhandled request error")
    return JSONResponse(
        status_code=500,
        content=error_payload("INTERNAL_ERROR", "Internal server error."),
    )


@app.exception_handler(HTTPException)
async def http_error(_request: Request, exc: HTTPException):
    content = exc.detail if isinstance(exc.detail, dict) else error_payload("INTERNAL_ERROR", str(exc.detail))
    return JSONResponse(status_code=exc.status_code, content=content, headers=exc.headers)


@app.post("/detect-page-preview", dependencies=[Depends(require_service_token)])
async def detect_page_preview(file: UploadFile = File(...)):
    data = await file.read()
    validate_upload(data, file.filename or "", {"image"})
    try:
        import cv2
        import numpy as np
        from server.camera_feedback import generate_feedback_instruction
        from server.page_detector import AUTO_CAPTURE_THRESHOLD, page_detector
    except ImportError as exc:
        raise api_error(503, "INTERNAL_ERROR", "Computer vision dependencies are unavailable.") from exc
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise api_error(400, "INVALID_UPLOAD", "Invalid image.")
    height, width = image.shape[:2]
    result = page_detector.detect_page(image, mode="preview")
    return {
        "page_detected": result["page_detected"],
        "confidence": result["confidence"],
        "corners": result["corners"],
        "instruction": generate_feedback_instruction(result, width, height),
        "capture_ready": (
            result["confidence"] >= AUTO_CAPTURE_THRESHOLD
            and result["decision"] != "no_page_detected"
        ),
        "method": result["method"],
    }


async def _legacy_enqueue(
    *,
    file: UploadFile,
    db: Session,
    job_type: str,
    allowed: set[str],
    artifact_kind: str,
    extension: str,
):
    data = await file.read()
    content_type = validate_upload(data, file.filename or "", allowed)
    document = create_document(
        db,
        course=None,
        language=None,
        title=f"Legacy {job_type.lower()} upload",
    )
    page = DocumentPage(
        id=new_id("page"),
        document_id=document.id,
        page_number=1,
        status="UPLOADED",
    )
    db.add(page)
    db.flush()
    job = create_job(
        db,
        job_type=job_type,
        document_id=document.id,
        page_id=page.id,
        payload={},
    )
    source = artifacts.save_bytes(
        db,
        data,
        kind=artifact_kind,
        content_type=content_type,
        job_id=job.id,
        document_id=document.id,
        page_id=page.id,
        extension=extension,
    )
    page.source_artifact_id = source.id
    job.result_json = json.dumps({"source_artifact_id": source.id, "legacy": True})
    document.status = "PROCESSING"
    document.active_job_id = job.id
    db.commit()
    return {
        "job_id": job.id,
        "status": "pending",
        "durable_status": job.status,
        "message": "Upload successful. Processing queued.",
    }


@app.post("/process-still", dependencies=[Depends(require_service_token)])
async def process_still(file: UploadFile = File(...), db: Session = Depends(get_db)):
    return await _legacy_enqueue(
        file=file,
        db=db,
        job_type="PROCESS_STILL",
        allowed={"image"},
        artifact_kind="source_image",
        extension=".jpg",
    )


@app.post("/process-sweep", dependencies=[Depends(require_service_token)])
async def process_sweep(file: UploadFile = File(...), db: Session = Depends(get_db)):
    return await _legacy_enqueue(
        file=file,
        db=db,
        job_type="PROCESS_SWEEP",
        allowed={"zip"},
        artifact_kind="source_zip",
        extension=".zip",
    )
