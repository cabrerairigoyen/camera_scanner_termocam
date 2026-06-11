import json
import logging
import os
import random
import shutil
import socket
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.models import Artifact, Document, DocumentPage, Job, JobStep, Question
from server.repositories.documents import ordered_pages
from server.repositories.jobs import claim_next_job, reclaim_expired
from server.services import artifacts
from server.services.debug_reports import save_debug_report
from server.services.events import emit_event
from server.services.ids import new_id
from server.services.jobs import create_job, job_payload, set_job_payload
from server.services.solver_client import SolverClient, SolverUnavailable


LOGGER = logging.getLogger("termocam.worker")
LEGACY_JOBS_DIR = Path(__file__).resolve().parents[1] / "data" / "jobs"


class RecoverableJobError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class WorkerService:
    def __init__(self, session_factory, worker_id: str | None = None, lease_seconds: int = 120):
        self.session_factory = session_factory
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}"
        self.lease_seconds = lease_seconds
        self.handlers = {
            "PROCESS_STILL": self._process_still,
            "PROCESS_SWEEP": self._process_sweep,
            "PAGE_QUALITY": self._page_quality,
            "PAGE_OCR": self._page_ocr,
            "DOCUMENT_FINALIZE": self._document_finalize,
            "SOLVER_DISPATCH": self._solver_dispatch,
            "TTS_GENERATE": self._tts_generate,
        }

    def recover(self) -> int:
        with self.session_factory() as db:
            return reclaim_expired(db)

    def run_once(self) -> bool:
        with self.session_factory() as db:
            job = claim_next_job(db, self.worker_id, self.lease_seconds)
            if not job:
                return False
            if job.status == "CANCEL_REQUESTED":
                self._cancel(db, job)
                return True
            handler = self.handlers.get(job.type)
            if handler is None:
                self._fail(db, job, "INTERNAL_ERROR", f"No handler for {job.type}", recoverable=False)
                return True
            try:
                with self._heartbeat(job.id):
                    handler(db, job)
                db.refresh(job)
                if job.status == "CANCEL_REQUESTED":
                    self._cancel(db, job)
                else:
                    job.status = "SUCCEEDED"
                    job.progress = 1.0
                    job.current_step = None
                    job.finished_at = utcnow()
                    job.lease_owner = None
                    job.lease_expires_at = None
                    if job.document_id and job.type in {"PROCESS_STILL", "PROCESS_SWEEP"}:
                        document = db.get(Document, job.document_id)
                        document.status = "DONE"
                        document.finished_at = utcnow()
                    db.flush()
                    save_debug_report(db, job, self._quality_for_job(db, job))
                    self._success_event(db, job)
                    db.commit()
            except RecoverableJobError as exc:
                db.rollback()
                job = db.get(Job, job.id)
                self._fail(db, job, exc.code, str(exc), recoverable=True)
            except Exception as exc:
                LOGGER.exception("job failed", extra={"job_id": job.id, "worker_id": self.worker_id})
                db.rollback()
                job = db.get(Job, job.id)
                self._fail(db, job, "INTERNAL_ERROR", str(exc), recoverable=False)
            return True

    @contextmanager
    def _heartbeat(self, job_id: str):
        stopped = threading.Event()
        interval = max(5.0, self.lease_seconds / 3)

        def beat():
            while not stopped.wait(interval):
                with self.session_factory() as heartbeat_db:
                    heartbeat_job = heartbeat_db.get(Job, job_id)
                    if not heartbeat_job or heartbeat_job.status != "RUNNING":
                        return
                    heartbeat_job.heartbeat_at = utcnow()
                    heartbeat_job.lease_expires_at = utcnow() + timedelta(seconds=self.lease_seconds)
                    heartbeat_db.commit()

        thread = threading.Thread(target=beat, daemon=True, name=f"heartbeat-{job_id}")
        thread.start()
        try:
            yield
        finally:
            stopped.set()
            thread.join(timeout=1)

    def _step(self, db: Session, job: Job, name: str, progress: float):
        job.current_step = name
        job.progress = progress
        job.heartbeat_at = utcnow()
        job.lease_expires_at = utcnow() + timedelta(seconds=self.lease_seconds)
        step = JobStep(
            id=new_id("step"),
            job_id=job.id,
            name=name,
            status="RUNNING",
            attempt=job.attempt,
            started_at=utcnow(),
        )
        db.add(step)
        db.commit()
        return step

    def _finish_step(self, db: Session, step: JobStep, *, metrics: dict | None = None, warnings=None):
        step.status = "SUCCEEDED"
        step.finished_at = utcnow()
        step.duration_ms = int((step.finished_at - step.started_at).total_seconds() * 1000)
        step.metrics_json = json.dumps(metrics or {})
        step.warnings_json = json.dumps(warnings or [])
        db.commit()

    def _source_artifact(self, db: Session, job: Job) -> Artifact:
        source_id = job_payload(job).get("source_artifact_id")
        artifact = db.get(Artifact, source_id) if source_id else None
        if not artifact:
            raise RecoverableJobError("STORAGE_UNAVAILABLE", "Source artifact is missing")
        return artifact

    def _register_legacy_outputs(self, db: Session, job: Job, output_dir: Path) -> None:
        mapping = {
            "reconstructed.jpg": ("reconstructed_jpg", "image/jpeg"),
            "reconstructed.pdf": ("reconstructed_pdf", "application/pdf"),
            "ocr.json": ("ocr_json", "application/json"),
            "page_detection_overlay.jpg": ("overlay_image", "image/jpeg"),
        }
        for filename, (kind, content_type) in mapping.items():
            path = output_dir / filename
            if path.is_file():
                artifact = artifacts.save_bytes(
                    db,
                    path.read_bytes(),
                    kind=kind,
                    content_type=content_type,
                    job_id=job.id,
                    document_id=job.document_id,
                    page_id=job.page_id,
                    extension=path.suffix,
                )
                if kind == "ocr_json" and job.page_id:
                    page = db.get(DocumentPage, job.page_id)
                    page.ocr_artifact_id = artifact.id
                    data = json.loads(path.read_text())
                    page.ocr_text = data.get("text", "")
                    page.status = "OCR_DONE"
                    page.accepted = True

    def _process_still(self, db: Session, job: Job):
        source = self._source_artifact(db, job)
        step = self._step(db, job, "reconstruction", 0.1)
        output_dir = LEGACY_JOBS_DIR / job.id
        output_dir.mkdir(parents=True, exist_ok=True)
        from server.process_still import process_highres_still

        process_highres_still(str(artifacts.artifact_path(source)), job.id, str(LEGACY_JOBS_DIR))
        self._register_legacy_outputs(db, job, output_dir)
        self._finish_step(db, step)

    def _process_sweep(self, db: Session, job: Job):
        source = self._source_artifact(db, job)
        step = self._step(db, job, "reconstruction", 0.1)
        from server.process_sweep import process_sweep_zip

        report = process_sweep_zip(str(artifacts.artifact_path(source)), job.id, str(LEGACY_JOBS_DIR))
        if report.get("status") != "SUCCEEDED":
            raise RecoverableJobError("INTERNAL_ERROR", "Sweep reconstruction failed")
        self._register_legacy_outputs(db, job, LEGACY_JOBS_DIR / job.id)
        self._finish_step(db, step, metrics=report.get("quality", {}))

    def _page_quality(self, db: Session, job: Job):
        page = db.get(DocumentPage, job.page_id)
        source = self._source_artifact(db, job)
        page.status = "QUALITY_CHECK_RUNNING"
        step = self._step(db, job, "page_quality", 0.1)
        if source.kind == "source_zip":
            metrics = {"capture_mode": "sweep", "quality_score": 1.0}
            warnings = []
            accepted = True
        else:
            try:
                import cv2
                from server.capture_quality import evaluate_capture_quality
                from server.page_detector import page_detector
            except ImportError as exc:
                raise RecoverableJobError("INTERNAL_ERROR", "Computer vision dependencies are unavailable") from exc
            image = cv2.imread(str(artifacts.artifact_path(source)))
            if image is None:
                raise RecoverableJobError("INVALID_UPLOAD", "Uploaded image cannot be decoded")
            detection = page_detector.detect_page(image, mode="preview")
            quality = evaluate_capture_quality(
                image, detection.get("confidence", 0.0), detection.get("corners") or []
            )
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            laplacian_variance = float(cv2.Laplacian(gray, cv2.CV_64F).var())
            metrics = {
                "laplacian_variance": laplacian_variance,
                "sharpness": quality["sharpness"],
                "lighting": quality["lighting"],
                "geometry": quality["geometry"],
                "page_confidence": detection.get("confidence", 0.0),
                "text_coverage": 0.0,
                "quality_score": quality["score"],
            }
            warnings = []
            if quality["sharpness"] < 0.2:
                warnings.append("blurry")
            if detection.get("area_ratio", 0.0) > 0.9 or detection.get("decision") == "not_safe_to_warp":
                warnings.append("page_cropped")
            accepted = quality["sharpness"] >= 0.1 and "page_cropped" not in warnings
        page.metrics_json = json.dumps(metrics)
        page.warnings_json = json.dumps(warnings)
        page.quality_score = metrics["quality_score"]
        page.accepted = accepted
        page.status = "ACCEPTED" if accepted else "REJECTED"
        page.rejection_reason = None if accepted else (warnings[0] if warnings else "quality_failed")
        artifacts.save_json(
            db,
            {
                "page_number": page.page_number,
                "accepted": accepted,
                "reason": page.rejection_reason,
                "quality_score": page.quality_score,
                "metrics": metrics,
                "warnings": warnings,
                "recommended_action": None if accepted else "retake_page",
            },
            kind="quality_json",
            job_id=job.id,
            document_id=job.document_id,
            page_id=page.id,
        )
        if accepted:
            create_job(
                db,
                job_type="PAGE_OCR",
                document_id=job.document_id,
                page_id=page.id,
                payload={"source_artifact_id": source.id},
                idempotency_key=f"page-ocr:{page.id}:{source.sha256}",
            )
            key, severity, spoken = "page_accepted", "success", f"Page {page.page_number} accepted."
        else:
            key = "page_blurry" if "blurry" in warnings else "page_cropped"
            severity, spoken = "warning", f"Page {page.page_number} needs to be retaken."
        emit_event(
            db,
            document_id=job.document_id,
            page_id=page.id,
            job_id=job.id,
            event_type="AUDIO_FEEDBACK",
            severity=severity,
            message_key=key,
            spoken_text=spoken,
            dedupe_key=f"{page.id}:{key}:{source.sha256}",
            payload={"page_number": page.page_number, "metrics": metrics},
        )
        self._finish_step(db, step, metrics=metrics, warnings=warnings)

    def _page_ocr(self, db: Session, job: Job):
        page = db.get(DocumentPage, job.page_id)
        page.status = "OCR_RUNNING"
        db.commit()
        source = self._source_artifact(db, job)
        if source.kind == "source_zip":
            self._process_sweep(db, job)
        else:
            self._process_still(db, job)
        ocr_artifact = artifacts.latest_artifact(db, job.id, "ocr_json")
        ocr_data = json.loads(artifacts.artifact_path(ocr_artifact).read_text()) if ocr_artifact else {}
        confidences = [line.get("confidence", 0.0) for line in ocr_data.get("lines", [])]
        mean_conf = sum(confidences) / len(confidences) if confidences else 0.0
        metrics = json.loads(page.metrics_json or "{}")
        metrics["ocr_confidence"] = mean_conf
        metrics["text_coverage"] = min(len(ocr_data.get("text", "")) / 2000.0, 1.0)
        page.metrics_json = json.dumps(metrics)
        warnings = json.loads(page.warnings_json or "[]")
        if not ocr_data.get("text", "").strip():
            warnings.append("empty_ocr")
        elif mean_conf < float(os.getenv("MIN_OCR_CONFIDENCE", "0.7")):
            warnings.append("low_ocr_confidence")
        page.warnings_json = json.dumps(sorted(set(warnings)))
        if "low_ocr_confidence" in warnings or "empty_ocr" in warnings:
            emit_event(
                db,
                document_id=job.document_id,
                page_id=page.id,
                job_id=job.id,
                event_type="AUDIO_FEEDBACK",
                severity="warning",
                message_key="low_ocr_confidence",
                spoken_text=f"Page {page.page_number} text quality is low. Retake page.",
                dedupe_key=f"{page.id}:low-ocr:{source.sha256}",
                payload={"page_number": page.page_number, "ocr_confidence": mean_conf},
            )
        db.commit()

    def _document_finalize(self, db: Session, job: Job):
        document = db.get(Document, job.document_id)
        pages = [page for page in ordered_pages(db, document.id) if page.accepted and page.status == "OCR_DONE"]
        if not pages:
            raise RecoverableJobError("OCR_EMPTY", "No accepted OCR-complete pages are available")
        step = self._step(db, job, "document_finalize", 0.2)
        merged_pages = []
        images = []
        for page in pages:
            ocr_artifact = db.get(Artifact, page.ocr_artifact_id)
            if not ocr_artifact:
                raise RecoverableJobError("STORAGE_UNAVAILABLE", f"OCR artifact missing for page {page.page_number}")
            ocr = json.loads(artifacts.artifact_path(ocr_artifact).read_text())
            merged_pages.append(
                {
                    "page_id": page.id,
                    "page_number": page.page_number,
                    "text": ocr.get("text", ""),
                    "mean_confidence": json.loads(page.metrics_json or "{}").get("ocr_confidence"),
                    "blocks": ocr.get("lines", []),
                }
            )
            image_artifact = db.scalar(
                select(Artifact)
                .where(Artifact.page_id == page.id, Artifact.kind == "reconstructed_jpg")
                .order_by(Artifact.created_at.desc())
            )
            if image_artifact:
                images.append(artifacts.artifact_path(image_artifact))
        merged = {
            "document_id": document.id,
            "text": "\n\n".join(page["text"] for page in merged_pages),
            "pages": merged_pages,
        }
        artifacts.save_json(
            db, merged, kind="ocr_json", job_id=job.id, document_id=document.id
        )
        if images:
            from PIL import Image
            opened = [Image.open(path).convert("RGB") for path in images]
            import io
            output = io.BytesIO()
            opened[0].save(output, "PDF", save_all=True, append_images=opened[1:])
            for image in opened:
                image.close()
            artifacts.save_bytes(
                db,
                output.getvalue(),
                kind="reconstructed_pdf",
                content_type="application/pdf",
                job_id=job.id,
                document_id=document.id,
                extension=".pdf",
            )
        payload = job_payload(job)
        if payload.get("solve"):
            create_job(
                db,
                job_type="SOLVER_DISPATCH",
                document_id=document.id,
                payload={"answer_mode": payload.get("answer_mode", "standard"), "document_version": document.version},
                idempotency_key=f"solver:{document.id}:{document.version}:{payload.get('answer_mode', 'standard')}",
            )
        document.status = "DONE"
        document.finished_at = utcnow()
        self._finish_step(db, step, metrics={"page_count": len(pages)})

    def _solver_dispatch(self, db: Session, job: Job):
        document = db.get(Document, job.document_id)
        pages = ordered_pages(db, document.id)
        questions = list(db.scalars(select(Question).where(Question.document_id == document.id)))
        payload_data = job_payload(job)
        request = {
            "schema_version": "1.0",
            "source": "termocam",
            "document_id": document.id,
            "document_version": payload_data.get("document_version", document.version),
            "course": document.course,
            "language": document.language,
            "answer_mode": payload_data.get("answer_mode", "standard"),
            "pages": [
                {
                    "page_id": page.id,
                    "page_number": page.page_number,
                    "ocr_text": page.ocr_text or "",
                    "ocr_blocks": [],
                    "quality": {
                        "accepted": page.accepted,
                        "ocr_confidence": json.loads(page.metrics_json or "{}").get("ocr_confidence"),
                    },
                }
                for page in pages if page.accepted
            ],
            "questions": [
                {
                    "id": question.stable_question_id,
                    "type": question.type,
                    "text": question.text,
                    "choices": json.loads(question.choices_json) if question.choices_json else None,
                }
                for question in questions
            ],
            "callback_url": os.getenv("TERMOCAM_CALLBACK_BASE_URL", "").rstrip("/") + f"/solver-callbacks/{job.id}",
            "requested_outputs": ["answers", "citations", "audio"],
        }
        step = self._step(db, job, "solver_dispatch", 0.2)
        try:
            response = SolverClient().create_job(
                request,
                f"{document.id}:{document.version}:{request['answer_mode']}",
            )
        except SolverUnavailable as exc:
            raise RecoverableJobError("SOLVER_UNAVAILABLE", str(exc)) from exc
        set_job_payload(job, {**payload_data, "solver": response})
        self._finish_step(db, step, metrics={"solver_job_id": response.get("solver_job_id")})

    def _tts_generate(self, db: Session, job: Job):
        raise RecoverableJobError("TTS_FAILED", "TTS service is not configured")

    def _fail(self, db: Session, job: Job, code: str, message: str, recoverable: bool):
        now = utcnow()
        can_retry = recoverable and job.attempt < job.max_attempts
        if can_retry:
            delay = min(300, (2 ** max(job.attempt - 1, 0)) + random.uniform(0, 1))
            job.status = "RETRY_WAIT"
            job.lease_expires_at = now + timedelta(seconds=delay)
        else:
            job.status = "FAILED"
            job.finished_at = now
            job.lease_expires_at = None
        job.lease_owner = None
        job.error_code = code
        job.error_json = json.dumps(
            {
                "code": code,
                "message": message,
                "recoverable": recoverable,
                "recommended_action": "retake_page" if code.startswith("OCR_") else None,
            }
        )
        if job.document_id and job.type != "SOLVER_DISPATCH":
            document = db.get(Document, job.document_id)
            if document and document.status == "PROCESSING":
                document.status = "FAILED"
                document.finished_at = now
        db.flush()
        save_debug_report(db, job, self._quality_for_job(db, job))
        if job.document_id:
            emit_event(
                db,
                document_id=job.document_id,
                page_id=job.page_id,
                job_id=job.id,
                event_type="ERROR",
                severity="error",
                message_key="solver_unavailable" if code == "SOLVER_UNAVAILABLE" else "job_failed",
                spoken_text="Processing failed.",
                dedupe_key=f"{job.id}:failed:{job.attempt}",
                payload={"code": code},
            )
        db.commit()

    def _cancel(self, db: Session, job: Job):
        job.status = "CANCELLED"
        job.finished_at = utcnow()
        job.lease_owner = None
        job.lease_expires_at = None
        job.error_code = "JOB_CANCELLED"
        if job.document_id:
            document = db.get(Document, job.document_id)
            if document and document.active_job_id == job.id:
                document.status = "CANCELLED"
                document.finished_at = utcnow()
        db.commit()

    def _quality_for_job(self, db: Session, job: Job) -> dict:
        if not job.page_id:
            return {}
        page = db.get(DocumentPage, job.page_id)
        return json.loads(page.metrics_json or "{}") if page else {}

    def _success_event(self, db: Session, job: Job):
        if not job.document_id:
            return
        key = "processing_finished"
        spoken = "Processing finished."
        if job.type == "SOLVER_DISPATCH":
            key, spoken = "answers_ready", "Answers ready."
        emit_event(
            db,
            document_id=job.document_id,
            page_id=job.page_id,
            job_id=job.id,
            event_type="JOB_STATUS",
            severity="success",
            message_key=key,
            spoken_text=spoken,
            dedupe_key=f"{job.id}:succeeded",
        )


def utcnow():
    return datetime.now(timezone.utc)
