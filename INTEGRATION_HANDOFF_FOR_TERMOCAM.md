# Solver Repo Integration Handoff

> Inspection date: 2026-06-11  
> Scope: Git `HEAD` (`2df4983`) in `/workspaces/camera_scanner_termocam`
>
> **Identity warning:** this checkout is the **TermoCam Camera Scanner repository**, not the separate solver backend described in the request. The repository contains an optional bridge that expects a sibling `math_solver_backend`, but that sibling is not present in this workspace. Solver/RAG/LLM/TTS capabilities beyond the bridge are therefore **not inspectable and must not be treated as implemented here**.

## 1. Repository identity

* Project name: TermoCam Camera Scanner.
* Main purpose: Raspberry Pi document capture plus server-side reconstruction, rectification, OCR, PDF generation, and debug artifacts.
* Main entrypoints:
  * `pi/live_camera_server.py`: Flask edge/camera API.
  * `server/app.py`: FastAPI reconstruction API.
  * `server/process_sweep.py`: sweep ZIP pipeline.
  * `server/process_still.py`: still-image pipeline.
  * `server/solver_bridge.py`: optional bridge to an external sibling solver.
  * `termocam/pi/live_camera_server.py`: older/parallel Pi implementation; not referenced by the current README.
* How to run locally:
  * Documented only at a high level: install `pi/requirements-pi.txt` and `server/requirements-server.txt`.
  * Pi entrypoint: `python pi/live_camera_server.py`.
  * FastAPI likely entrypoint: `uvicorn server.app:app --host 0.0.0.0 --port 8000`.
  * The Uvicorn command is inferred from the FastAPI app and dependency; no exact launch command, service file, Dockerfile, or Compose file is present.
  * Current checkout cannot be run as-is because `pi.capture.*` modules imported by the Pi app, sweep pipeline, and tests are absent. `python-dotenv` and `requests` are also used by server code but omitted from `server/requirements-server.txt`.
* Main server framework:
  * FastAPI for reconstruction.
  * Flask for Raspberry Pi capture/control.
* Main dependencies:
  * FastAPI, Uvicorn, python-multipart, NumPy, OpenCV, Pillow, pytesseract, Jinja2, pytest.
  * Pi: Flask, PyYAML, NumPy, OpenCV headless, requests, psutil.
  * Optional/runtime-referenced: PaddleOCR, PaddlePaddle, Mathpix HTTP API, Ultralytics YOLO.
  * Missing declarations: `python-dotenv` and server-side `requests`.
* Environment variables:
  * `AUTO_SOLVE_AFTER_OCR`
  * `MATHPIX_APP_ID`
  * `MATHPIX_API_ID`
  * `MATHPIX_APP_KEY`
  * `MIN_OCR_CONFIDENCE`
  * `SOLVER_SEND_TO_DISPLAY`
  * `SOLVER_TIMEOUT_SECONDS`
  * `USE_CO_SCIENTIST`
  * `PYTHONPATH` is set internally for the solver subprocess.
* Secrets detected:
  * Secret-bearing variable names: `MATHPIX_APP_ID`, `MATHPIX_API_ID`, `MATHPIX_APP_KEY`.
  * No secret values were printed or found in tracked `.env` files.
  * `.env`, `.env.*`, and `config.yaml` are ignored.
  * `termocam/config.py` contains hard-coded Google Cloud project/processor identifiers. These are identifiers rather than credentials, but should still move to environment configuration.

## 2. Architecture overview

The real implemented architecture is a two-process scanner system with an optional, loosely coupled external solver:

```text
browser/operator
      |
      v
Flask Pi camera API
      |
      +--> camera lock/state machine
      +--> local still or sweep capture
      +--> local session manifest/ZIP   [implementation modules missing]
      |
      v HTTP multipart through hard-coded localhost tunnel
FastAPI reconstruction API
      |
      +--> in-memory job_statuses
      +--> FastAPI BackgroundTasks
      |
      v
OpenCV reconstruction pipeline
stitch -> page detect/rectify -> enhance -> OCR -> PDF
      |
      v
server/data/jobs/<job_id>/
      |
      +--> reconstructed.jpg
      +--> reconstructed.pdf
      +--> ocr.json
      +--> debug_report.json
      |
      +--> optional daemon thread
             |
             v
      external sibling math_solver_backend
      [not present; result contract not captured]
```

Practical characteristics:

* Edge/server separation is appropriate for a constrained Pi.
* Camera ownership uses process-local locks and explicit states.
* Reconstruction jobs are not durable records. Active status is a Python dictionary.
* `BackgroundTasks` is execution after an HTTP response, not a durable queue.
* Completed artifacts can be rediscovered from disk, but interrupted jobs cannot be resumed.
* The optional solver bridge sends extracted OCR text through a file and CLI arguments. It does not expose a solver API, persist solver status, or collect answer/audio artifacts.
* Documentation describes several missing `pi/capture/` modules as if present. This is documented-only behavior in this checkout.

## 3. API inventory

No route has authentication. All routes below are implemented unless marked otherwise.

### Reconstruction server

```text
GET /health
Purpose: Basic process liveness.
Input schema: none.
Output schema: {"status":"ok","uptime_sec": number}; uptime_sec is actually system monotonic time, not process uptime.
Sync or async: sync.
Creates job? no.
Requires auth? no.
Used for TermoCam integration? yes.
```

```text
POST /detect-page-preview
Purpose: Decode a preview image, detect page geometry, and return positioning feedback.
Input schema: multipart file containing an image.
Output schema: page_detected, confidence, corners, instruction, capture_ready, method.
Sync or async: async route, synchronous CPU work.
Creates job? no.
Requires auth? no.
Used for TermoCam integration? yes.
```

```text
POST /process-still
Purpose: Store one image and schedule still reconstruction/OCR.
Input schema: multipart file; filename extension must be jpg/jpeg/png.
Output schema: {"job_id": string, "status":"pending","message": string}.
Sync or async: async upload plus in-process BackgroundTasks.
Creates job? yes.
Requires auth? no.
Used for TermoCam integration? yes.
```

```text
POST /process-sweep
Purpose: Store one sweep ZIP and schedule reconstruction/OCR.
Input schema: multipart file; filename must end in .zip.
Output schema: {"job_id": string, "status":"pending","message": string}.
Sync or async: async upload plus in-process BackgroundTasks.
Creates job? yes.
Requires auth? no.
Used for TermoCam integration? yes.
```

```text
GET /jobs/{job_id}
Purpose: Return in-memory status or completed debug_report.json.
Input schema: path job_id.
Output schema: active status object or debug report; response shape changes at completion.
Sync or async: sync.
Creates job? no.
Requires auth? no.
Used for TermoCam integration? yes.
```

```text
GET /jobs/{job_id}/result/reconstructed.jpg
Purpose: Download final JPEG.
Input schema: path job_id.
Output schema: image/jpeg.
Sync or async: sync.
Creates job? no.
Requires auth? no.
Used for TermoCam integration? yes.
```

```text
GET /jobs/{job_id}/result/reconstructed.pdf
Purpose: Download final PDF.
Input schema: path job_id.
Output schema: application/pdf.
Sync or async: sync.
Creates job? no.
Requires auth? no.
Used for TermoCam integration? yes.
```

```text
GET /jobs/{job_id}/result/ocr.json
Purpose: Download normalized OCR output.
Input schema: path job_id.
Output schema: {"text": string, "lines":[{"text", "confidence", "bbox"}], "fields": object, "engine": string}.
Sync or async: sync.
Creates job? no.
Requires auth? no.
Used for TermoCam integration? yes.
```

```text
GET /jobs/{job_id}/result/debug_report.json
Purpose: Download pipeline summary.
Input schema: path job_id.
Output schema: job/session/input/stitching/quality/ocr/outputs/page_detection.
Sync or async: sync.
Creates job? no.
Requires auth? no.
Used for TermoCam integration? yes.
```

There are no implemented server routes for raw OCR text submission, structured question upload, answer retrieval, audio retrieval, retry, cancellation, events, or solver health.

### Pi edge server

```text
GET /
Purpose: Camera control HTML.
Input schema: none.
Output schema: text/html.
Sync or async: sync.
Creates job? no.
Requires auth? no.
Used for TermoCam integration? local UI only.
```

```text
GET /health
Purpose: Temperature, disk, and camera-state health.
Input schema: none.
Output schema: status, system_temp_c, free_disk_mb, state.
Sync or async: sync.
Creates job? no.
Requires auth? no.
Used for TermoCam integration? yes.
```

```text
GET /stream
Purpose: MJPEG preview while holding the camera lock.
Input schema: none.
Output schema: multipart MJPEG stream or 409 text.
Sync or async: streaming generator.
Creates job? no.
Requires auth? no.
Used for TermoCam integration? yes.
```

```text
GET /photo
Purpose: Capture and return a calibrated still.
Input schema: none.
Output schema: image/jpeg or JSON error.
Sync or async: sync.
Creates job? no.
Requires auth? no.
Used for TermoCam integration? legacy.
```

```text
POST /process-highres
Purpose: Capture a still and upload it to FastAPI /process-still.
Input schema: none.
Output schema: reconstruction server job response or local error.
Sync or async: sync and blocking upload.
Creates job? indirectly.
Requires auth? no.
Used for TermoCam integration? yes.
```

```text
POST /detect-page-preview
Purpose: Proxy a preview image to the FastAPI route.
Input schema: multipart file.
Output schema: upstream response.
Sync or async: sync.
Creates job? no.
Requires auth? no.
Used for TermoCam integration? yes.
```

```text
GET|POST /calibrate
Purpose: Capture an unwarped calibration image.
Input schema: none.
Output schema: image/jpeg or error.
Sync or async: sync.
Creates job? no.
Requires auth? no.
Used for TermoCam integration? yes.
```

```text
POST /sweep/start
Purpose: Start a local sweep capture thread after thermal/disk checks.
Input schema: optional interval_ms, max_frames, sharpness_threshold, min_frame_difference, jpeg_quality, upload_after_capture, resolution.
Output schema: {"session_id": string, "status":"running"}.
Sync or async: starts a custom thread inside missing SweepSession code.
Creates job? creates a Pi session, not a server job.
Requires auth? no.
Used for TermoCam integration? yes.
```

```text
POST /sweep/stop
Purpose: Stop active session and optionally launch daemon upload thread.
Input schema: none.
Output schema: delegated SweepSession stop result.
Sync or async: sync; optional daemon upload.
Creates job? indirectly if auto-upload succeeds.
Requires auth? no.
Used for TermoCam integration? yes.
```

```text
GET /sweep/status
Purpose: Return active sweep counters.
Input schema: none.
Output schema: status, current_session_id, accepted_frames, rejected_frames, last_error.
Sync or async: sync.
Creates job? no.
Requires auth? no.
Used for TermoCam integration? yes.
```

```text
GET /sweep/sessions
Purpose: List filesystem manifests.
Input schema: none.
Output schema: {"sessions":[...]}.
Sync or async: sync.
Creates job? no.
Requires auth? no.
Used for TermoCam integration? useful but should become document API.
```

```text
GET /sweep/{session_id}/manifest
GET /sweep/{session_id}/zip
POST /sweep/{session_id}/upload
DELETE /sweep/{session_id}
Purpose: Read, package, upload, or delete a local session.
Input schema: path session_id.
Output schema: JSON, ZIP, upstream upload result, or deletion result.
Sync or async: sync.
Creates job? upload can create one.
Requires auth? no.
Used for TermoCam integration? transitional only.
```

## 4. Job model

The server has no formal job model or schema.

* Job ID format:
  * Sweep: `job_<unix_seconds>_<6 hex chars>`.
  * Still: `job_still_<unix_seconds>_<6 hex chars>`.
  * Pi session: `sess_<unix_seconds>`; collision is possible for two starts in one second.
* Actual server states:

```text
pending
running
completed
failed
```

* Pi camera states:

```text
IDLE
STREAMING
SWEEP_RUNNING
CAPTURING_STILL
ERROR
```

* Where job state is stored: active server state in global `job_statuses`; completed pipeline details in `debug_report.json`.
* Whether jobs survive server restart:
  * Completed jobs with readable debug reports can be found by direct ID.
  * Pending/running/failed in-memory states do not survive.
  * There is no startup scan to classify interrupted uploads or partial job directories.
* Database-backed: no.
* Artifact storage: local disk under `server/data/jobs/<job_id>/`; uploads under `server/data/uploads/`.
* Progress tracking: no stage/progress percentage; only pending/running/completed/failed.
* Retry: no API and no automatic retry.
* Cancellation: none.
* Important correctness issue: the worker marks any job with a debug report as `completed`. `process_sweep_zip` writes a debug report even after pipeline failure, so failed sweep jobs can be reported as completed in memory.
* Important correctness issue: completed `GET /jobs/{job_id}` returns the debug report, which has no top-level `status`. Clients documented to poll `status` cannot reliably detect completion.
* Important correctness issue: `server/app.py` calls `json.load` without importing `json`.

## 5. Background queue / worker system

Detected mechanisms:

* FastAPI `BackgroundTasks` for still and sweep processing.
* A daemon `threading.Thread` for Pi auto-upload.
* A daemon `threading.Thread` for solver invocation.
* No Celery, RQ, Dramatiq, Redis, RabbitMQ, database queue, or external scheduler.

Behavior:

* Enqueue: upload is read fully into memory, written to disk, then a function is added to `BackgroundTasks`.
* Worker start: runs in the API process after the response. There is no separately managed worker.
* Retry: none.
* Timeouts:
  * Mathpix HTTP request: 30 seconds.
  * Pi still upload: 60 seconds.
  * Pi preview proxy: 2 seconds.
  * Solver subprocess: configurable, default 1,800 seconds.
  * Reconstruction itself has no job timeout.
* API server restart: active work is lost; uploaded files may remain; status dictionary is cleared.
* Worker crash: same process as API; no lease, heartbeat, acknowledgement, or redelivery.
* Queue backend down: not applicable because there is no backend.
* Multi-process Uvicorn risk: each process has a separate `job_statuses` dictionary.
* Solver daemon-thread risk: daemon threads terminate on process exit and their result is discarded.

Recommendation for TermoCam:

* Do not copy `BackgroundTasks` as the production job queue.
* For the current deployment size, use a database-backed custom worker with PostgreSQL `FOR UPDATE SKIP LOCKED`, or SQLite with a single worker if deployment is guaranteed single-host/single-worker.
* Prefer PostgreSQL if scanner and solver may scale independently.
* Store attempts, lease expiry, heartbeat, timeout, next retry time, and idempotency key.
* Keep Redis/Celery optional. Celery is justified only if the project already operates Redis/RabbitMQ and needs broad routing/scheduling. A DB queue is simpler and makes the job database the recovery source of truth.

## 6. Database design

There is no database, ORM, migration framework, table schema, or database dependency.

Existing filesystem concepts:

```text
name: server job directory
fields: path implied by job_id; reconstructed image/PDF, OCR JSON, debug report, optional bridge text
purpose: artifact storage
relationship: one directory per reconstruction job
useful for TermoCam? yes, as artifact storage behind database metadata
```

```text
name: Pi sweep manifest
fields: session_id, camera, capture_config, frames, rejected (based on tests/docs)
purpose: local capture session audit
relationship: points to frame files
useful for TermoCam? yes, but implementation modules are absent
```

Recommended TermoCam models:

```text
name: documents
fields: id, status, course, language, created_at, finished_at, active_job_id, version
purpose: multi-page user session
relationship: has many pages, jobs, events
useful for TermoCam? yes
```

```text
name: document_pages
fields: id, document_id, page_number, source_artifact_id, status, quality_score, accepted, rejection_reason, ocr_text, ocr_json_artifact_id, created_at
purpose: ordered page and quality/OCR state
relationship: belongs to document; has quality metrics
useful for TermoCam? yes
```

```text
name: jobs
fields: id, document_id, type, status, progress, attempt, max_attempts, priority, idempotency_key, lease_owner, lease_expires_at, heartbeat_at, error_code, error_json, created_at, started_at, finished_at
purpose: durable work queue and lifecycle
relationship: belongs to document; has steps, artifacts, events
useful for TermoCam? yes
```

```text
name: job_steps
fields: id, job_id, name, status, attempt, started_at, finished_at, duration_ms, warnings_json, error_json, metrics_json
purpose: restart/debug visibility per pipeline stage
relationship: belongs to job
useful for TermoCam? yes
```

```text
name: questions / answers
fields: stable question ID, document ID, type, text, choices JSON; answer text/choice, confidence, citations JSON, model metadata
purpose: solver input/output
relationship: questions belong to document; answers belong to question and solver job
useful for TermoCam? yes
```

```text
name: artifacts
fields: id, job_id, page_id, kind, storage_key, content_type, byte_size, sha256, created_at
purpose: safe indirection to disk/object storage
relationship: belongs to job and optionally page
useful for TermoCam? yes
```

```text
name: events
fields: id, document_id, job_id, page_id, sequence, event_type, severity, message_key, spoken_text, payload_json, created_at
purpose: polling/SSE and screenless audio feedback
relationship: belongs to document/job/page
useful for TermoCam? yes
```

## 7. Server restart recovery

Actual behavior:

* API restarts during job: background function/thread dies. Status is lost. No requeue.
* Worker restarts: there is no independent worker.
* Database restarts: no database.
* Queue restarts: no queue.
* File storage unavailable:
  * Upload write raises HTTP 500.
  * Artifact writes are inconsistently checked; several OpenCV writes ignore return values.
  * Debug report write catches and prints errors instead of failing the job.
* Completed job recovery: `GET /jobs/{id}` attempts to read a pre-existing report from disk, but currently lacks `import json`.
* Partial job recovery: not implemented.
* Pi restart during sweep: process-local state is lost. Existing manifests may remain, but no recovery logic is visible, and the required session implementation is missing.

Recommended recovery:

1. Commit a job row and upload artifact before enqueue.
2. Worker atomically leases `QUEUED` jobs.
3. Persist current step and heartbeat every 10-30 seconds.
4. On worker startup, move expired `RUNNING` leases to `RETRY_WAIT` or `FAILED` based on attempts.
5. Make each stage idempotent and write artifacts via temporary file plus atomic rename.
6. Checkpoint page OCR independently so a document retry does not repeat accepted pages.
7. Reconcile database artifacts with storage on startup.
8. Emit recovery events such as `job_requeued_after_restart`.

## 8. Multi-page document support

Implemented support:

* One sweep may contain multiple overlapping frames, but those frames reconstruct **one page**.
* The manifest preserves frame order through its `frames` array.
* OCR runs once on the reconstructed page.
* PDF generation creates a one-image PDF.

Not implemented:

* Multi-page PDFs as input.
* Multiple independent page images in one server job.
* Document/page entities.
* Explicit page numbers and reorder operations.
* Per-page OCR records in a document.
* Per-page acceptance workflow.
* Merged document text.
* Cross-page question extraction.
* One multi-page output PDF.

Reusable pattern:

* Reuse the idea of a manifest with ordered entries and per-entry metrics.
* Do not reuse the sweep manifest as the document model. A frame is not a page.

Recommended structure:

```json
{
  "document_id": "doc_...",
  "status": "CAPTURING",
  "pages": [
    {
      "page_id": "page_...",
      "page_number": 1,
      "status": "ACCEPTED",
      "source_artifact_id": "art_...",
      "quality": {},
      "ocr_artifact_id": "art_..."
    }
  ]
}
```

`finish` should lock page ordering, reject unfinished pages, create one document job, merge page OCR in page-number order, generate a multi-page PDF, then invoke the solver once with document-level context.

## 9. OCR confidence and quality checks

Implemented:

* Tesseract line confidence and line bounding boxes.
* PaddleOCR line confidence and quadrilateral boxes.
* Mathpix is normalized to one line with hard-coded confidence `1.0`; this is not a real confidence measurement.
* Sweep debug report stores mean OCR line confidence.
* Solver bridge skips low mean OCR confidence using `MIN_OCR_CONFIDENCE`.
* Capture quality functions calculate normalized sharpness, lighting, geometry, and a weighted score.
* Page detection reports confidence, corners, A4 geometry score, area ratio, decision, and reason.
* Pi start checks temperature and free disk.
* Directional preview feedback detects clipping, scale, centering, tilt, rotation, low confidence, and ready state.
* Still processing stores page detector hard negatives for review.

Gaps:

* `evaluate_capture_quality` is imported but not called by the API.
* No accepted/rejected page result schema.
* No text density, missing-corner flag, glare, shadow, motion blur, OCR coverage, language mismatch, or per-token confidence.
* No calibrated threshold policy per camera/resolution.
* No quality gates before committing a page.
* No page-level persistence.

Recommended page result:

```json
{
  "page_number": 3,
  "accepted": false,
  "reason": "blurry",
  "quality_score": 0.42,
  "metrics": {
    "laplacian_variance": 61.4,
    "sharpness": 0.18,
    "lighting": 0.91,
    "geometry": 0.77,
    "page_confidence": 0.83,
    "ocr_confidence": 0.54,
    "text_coverage": 0.11
  },
  "warnings": ["low_ocr_confidence", "page_cropped"],
  "recommended_action": "retake_page"
}
```

Quality should be split into capture-time checks, page-geometry checks, and post-OCR checks. Preserve raw metrics; do not store only one opaque score.

## 10. Logs and debug reports

Actual logging:

* Logging library: none; `print` and occasional `traceback.print_exc`.
* Format: unstructured text.
* Location: stdout/stderr only.
* Correlation IDs: job IDs are manually included in some pipeline messages.
* Per-job report: `debug_report.json`.
* Error traces: console only; not persisted in reports.
* Downloadable artifacts: report, OCR, PDF, JPEG; still pipeline also creates overlay, page quad, and rectification decision files, but there are no API routes for those.

Debug report strengths:

* Stable broad sections for input, stitching, quality, OCR, outputs, and page detection.
* Records stitching method and OpenCV status.
* Records mean/min sharpness and mean OCR confidence.

Debug report weaknesses:

* No top-level status, timestamps, durations, pipeline-step list, attempts, software version, configuration snapshot, error code, stack trace reference, or artifact hashes.
* A failed sweep still writes a report and is then marked completed by the worker.
* `solver_bridge_status` is added to `output_files` but dropped by `generate_debug_report`.
* Debug report writes swallow storage errors.
* Still pipeline calls `generate_debug_report` with the wrong keyword and missing required `session_id`, so that path is implemented but defective.

Recommended pattern:

```json
{
  "schema_version": "1.0",
  "job_id": "job_...",
  "document_id": "doc_...",
  "status": "FAILED",
  "created_at": "2026-06-11T14:00:00Z",
  "finished_at": "2026-06-11T14:00:04Z",
  "pipeline_steps": [
    {
      "name": "ocr",
      "status": "WARNING",
      "attempt": 1,
      "duration_ms": 2400,
      "warnings": [{"code": "LOW_OCR_CONFIDENCE"}],
      "metrics": {"mean_confidence": 0.61}
    }
  ],
  "errors": [],
  "artifacts": [{"kind": "ocr_json", "artifact_id": "art_..."}]
}
```

Use standard `logging` with JSON output and bind `request_id`, `document_id`, `job_id`, `page_id`, `step`, and `attempt`.

## 11. Audio / TTS / screenless feedback

Implemented:

* No TTS engine, MP3 generation, audio storage, or audio endpoint.
* `camera_feedback.generate_feedback_instruction` provides useful machine-readable message keys:

```text
no_page_detected
move_farther
move_closer
move_left
move_right
move_up
move_down
reduce_tilt
rotate_counterclockwise
rotate_clockwise
hold_still
ready
```

These keys are the strongest direct pattern for screenless feedback.

Recommended event schema:

```json
{
  "event_id": "evt_01J...",
  "sequence": 42,
  "event_type": "AUDIO_FEEDBACK",
  "severity": "success",
  "message_key": "page_accepted",
  "spoken_text": "Page 2 accepted.",
  "document_id": "doc_123",
  "job_id": "job_123",
  "page_number": 2,
  "dedupe_key": "doc_123:page_2:accepted",
  "created_at": "2026-06-11T14:00:00Z",
  "expires_at": null,
  "payload": {}
}
```

Design recommendations:

* Persist events, expose polling and SSE, and use monotonic per-document sequence numbers.
* Pi A should synthesize short operational messages locally so cloud loss can still say “Cloud offline.”
* Server-generated TTS should be reserved for solver answers or long content.
* Add cooldown/deduplication for repeated positioning feedback.
* Suggested message keys: `page_accepted`, `page_blurry`, `page_cropped`, `cloud_offline`, `processing_started`, `answers_ready`, `camera_disconnected`, `resync_complete`, `job_failed`, `retake_page`.

## 12. Solver/RAG/LLM/TTS interface

This is the most important limitation: the actual solver repository is absent.

Verified bridge input:

* Receives the scanner OCR dictionary.
* Extracts the first non-empty top-level `text`, `latex`, `markdown`, or `full_text`; otherwise joins `lines[].text`.
* Rejects empty text.
* Rejects mean line confidence below `MIN_OCR_CONFIDENCE`, default `0.7`.
* Legacy mode writes `ocr_text_for_solver.txt` and invokes:

```text
python process_document_to_brain.py <dummy_image>
  --skip-ocr
  --ocr-text <path>
  [--skip-display]
```

* Co-scientist mode calls `run_co_scientist(ocr_text=..., send_to_display=...)`.
* The bridge expects a sibling `math_solver_backend` and optionally `open_ai_co_scientist`/`open-ai-co-scientist`.

Verified bridge output:

* Only launch/return-code/error metadata is returned internally.
* Non-blocking execution discards the runner result.
* No answer JSON is copied into the scanner job.
* No citations, confidence, question IDs, QCM choices, answer style, reprocess operation, or audio contract is present.

Capability status:

* Raw OCR text: supported by bridge.
* Structured questions: not supported here.
* PDF/image solver input: not used by bridge, except a dummy positional path required by the external CLI.
* QCM: not inspectable.
* Short written answers: not inspectable.
* RAG: not inspectable.
* Citations/sources: not inspectable.
* Solver confidence: not inspectable.
* Audio: not inspectable.
* Reprocess/shorter/detailed answer: not inspectable.

Recommended clean contract between TermoCam and solver:

```http
POST /v1/solve-jobs
Idempotency-Key: <document-version-and-mode>
Authorization: Bearer <service-token>
Content-Type: application/json
```

```json
{
  "schema_version": "1.0",
  "source": "termocam",
  "document_id": "doc_123",
  "document_version": 3,
  "course": "GFN252",
  "language": "fr",
  "answer_mode": "standard",
  "pages": [
    {
      "page_id": "page_1",
      "page_number": 1,
      "ocr_text": "...",
      "ocr_blocks": [],
      "quality": {"accepted": true, "ocr_confidence": 0.91}
    }
  ],
  "questions": [
    {
      "id": "open_01",
      "type": "open",
      "text": "...",
      "choices": null
    },
    {
      "id": "qcm_02_01",
      "type": "qcm",
      "text": "...",
      "choices": {"A": "...", "B": "...", "C": "..."}
    }
  ],
  "callback_url": "https://termocam.internal/v1/solver-callbacks/job_...",
  "requested_outputs": ["answers", "citations", "audio"]
}
```

Response:

```json
{
  "solver_job_id": "solve_123",
  "status": "QUEUED",
  "accepted_document_version": 3,
  "status_url": "/v1/solve-jobs/solve_123"
}
```

Final result:

```json
{
  "solver_job_id": "solve_123",
  "status": "DONE",
  "answers": [
    {
      "question_id": "qcm_02_01",
      "answer_type": "qcm",
      "selected_choices": ["B"],
      "answer_text": "B",
      "confidence": 0.87,
      "citations": [
        {"page_number": 1, "quote": "...", "bbox": null, "source_id": "course_note_42"}
      ],
      "audio_artifact_id": "art_audio_..."
    }
  ],
  "warnings": [],
  "model": {"provider": "...", "name": "...", "prompt_version": "..."}
}
```

Use HTTP/JSON or a shared queue, not a sibling filesystem path and daemon subprocess. Keep scanner job IDs and solver job IDs separate and linked in the database.

## 13. Security and secrets

Actual state:

* Authentication: none.
* API keys: Mathpix credentials read from environment.
* CORS: wildcard origins, methods, and headers with credentials enabled.
* Upload restrictions: filename-extension checks only.
* Upload size: no limit; files are read fully into memory.
* MIME/content validation: not meaningful.
* Rate limiting: none.
* Path traversal:
  * `job_id` and Pi `session_id` are joined directly into filesystem paths.
  * Manifest frame filenames are joined directly under the extraction directory.
* ZIP extraction: `extractall` is used without zip-slip validation, member count/size limits, compression-ratio checks, or symlink checks.
* Deletion: unauthenticated Pi route recursively deletes a path derived from `session_id`.
* Upstream URL: currently hard-coded localhost tunnel, reducing SSRF exposure but reducing configurability.
* Solver bridge logs a preview of OCR text, which may expose document content.
* Error responses may reveal internal exception strings and paths.

Required controls:

1. Service-to-service authentication with rotated bearer token or mTLS.
2. Explicit CORS allowlist; disable browser credentials unless required.
3. UUID/ULID validation for IDs and storage lookup through artifact records.
4. Stream uploads to disk with maximum image/ZIP/PDF sizes.
5. Verify magic bytes and decode images before acceptance.
6. Safe ZIP extraction with normalized-path containment and expansion limits.
7. Rate limits and per-device quotas.
8. Never expose raw filesystem paths.
9. Redact OCR/document content from normal logs.
10. Encrypt transport and protect stored student/document data with retention controls.
11. Keep credentials only in secret storage/environment, not tracked Python modules.

## 14. Error handling and failure modes

Actual handling:

* Bad file extension: HTTP 400 for still/sweep.
* Invalid preview image: HTTP 500 JSON `{"error": ...}` rather than 422.
* Missing manifest/frames: pipeline catches error and writes warning in debug report.
* Missing OCR engine: returns mock OCR and can allow the pipeline to appear successful.
* Mathpix failure: falls back to Paddle/Tesseract.
* LLM/RAG/TTS failure: no implementation here.
* Solver failure: blocking helper captures return code/error; non-blocking path loses result.
* Timeout: solver and selected HTTP calls only.
* Duplicate request: no idempotency handling.
* Retry: none.
* Cancel: none.
* Malformed question JSON: no question endpoint.
* File write failures: inconsistently propagated.

Known defects affecting failure behavior:

* Missing `pi.capture` package prevents sweep/Pi imports.
* Missing `json` import breaks completed job lookup.
* Still pipeline calls `run_ocr` with two arguments although it accepts one.
* Still pipeline calls `generate_debug_report` with an invalid keyword and omits `session_id`.
* Worker equates report existence with success.
* Missing `python-dotenv` and server `requests` declarations.

Recommended shared error schema:

```json
{
  "status": "FAILED",
  "error": {
    "code": "OCR_LOW_CONFIDENCE",
    "message": "Page 3 OCR confidence is too low.",
    "recoverable": true,
    "recommended_action": "retake_page",
    "details": {"page_number": 3, "ocr_confidence": 0.42},
    "retry_after_seconds": null,
    "trace_id": "trace_..."
  }
}
```

Use stable codes such as `INVALID_UPLOAD`, `ZIP_UNSAFE`, `PAGE_BLURRY`, `PAGE_CROPPED`, `OCR_EMPTY`, `OCR_LOW_CONFIDENCE`, `SOLVER_UNAVAILABLE`, `SOLVER_TIMEOUT`, `TTS_FAILED`, `STORAGE_UNAVAILABLE`, `JOB_CONFLICT`, `JOB_NOT_RETRYABLE`, and `JOB_CANCELLED`.

## 15. What TermoCam should copy

```text
Feature: edge/heavy-compute partition
Existing implementation in this repo: Flask camera service sends still/sweep input to FastAPI reconstruction.
Files/classes/functions: pi/live_camera_server.py; server/app.py.
Why useful for TermoCam: keeps Pi CPU/RAM and thermal load low.
How to adapt: retain the boundary but formalize authenticated device/server APIs.
Risk: hard-coded tunnel URL and missing capture modules.
```

```text
Feature: camera ownership state machine
Existing implementation in this repo: CameraState plus state_lock/camera_lock.
Files/classes/functions: pi/live_camera_server.py CameraState and routes.
Why useful for TermoCam: prevents stream/capture contention.
How to adapt: persist only operational events, keep lock local, add startup reset and watchdog.
Risk: process-local globals do not work with multiple Flask workers.
```

```text
Feature: OCR normalization
Existing implementation in this repo: Mathpix/Paddle/Tesseract mapped to text/lines/confidence/bbox.
Files/classes/functions: server/ocr.py.
Why useful for TermoCam: gives downstream solver one engine-neutral contract.
How to adapt: add page/block/token IDs, language, calibrated confidence, and engine metadata.
Risk: Mathpix confidence is fabricated as 1.0; mock OCR must not count as success.
```

```text
Feature: capture/page quality signals
Existing implementation in this repo: sharpness, lighting, geometry, detector confidence, area ratio.
Files/classes/functions: server/capture_quality.py; server/page_detector.py; server/rectify.py.
Why useful for TermoCam: supports immediate retake decisions.
How to adapt: call it in the page endpoint, persist raw metrics, calibrate thresholds.
Risk: current aggregate function is unused and not validated against real captures.
```

```text
Feature: machine-readable screenless feedback keys
Existing implementation in this repo: directional enum strings.
Files/classes/functions: server/camera_feedback.py generate_feedback_instruction.
Why useful for TermoCam: clean input to local speech/audio prompts.
How to adapt: wrap keys in durable events with severity, sequence, page/job IDs, and cooldown.
Risk: no localization, persistence, or deduplication.
```

```text
Feature: per-job artifact directory
Existing implementation in this repo: server/data/jobs/<job_id>/.
Files/classes/functions: server/process_sweep.py; server/process_still.py.
Why useful for TermoCam: simple artifact grouping and support bundles.
How to adapt: database artifact records, safe storage keys, atomic writes, retention.
Risk: filesystem alone is not a job database.
```

```text
Feature: debug report sections and visual review artifacts
Existing implementation in this repo: stitching/quality/OCR/page detection plus overlays and hard negatives.
Files/classes/functions: server/debug_report.py; server/process_still.py.
Why useful for TermoCam: makes CV failures diagnosable and supports detector improvement.
How to adapt: add step timing/status/errors and expose artifacts through IDs.
Risk: current status semantics and still path are defective.
```

```text
Feature: confidence gate before solver
Existing implementation in this repo: mean OCR confidence threshold and empty-text guard.
Files/classes/functions: server/solver_bridge.py forward_ocr_to_solver.
Why useful for TermoCam: avoids expensive, misleading solver runs on bad scans.
How to adapt: page-aware policy with explicit recoverable errors and operator override.
Risk: unweighted mean confidence and confidence differences across OCR engines.
```

```text
Feature: stitching fallback hierarchy
Existing implementation in this repo: SCANS, PANORAMA, then custom SIFT/ORB homography.
Files/classes/functions: server/stitch.py.
Why useful for TermoCam: resilient sweep reconstruction.
How to adapt: preserve metrics and add stage timeout/memory limits.
Risk: custom blending is basic and no performance tests are present.
```

## 16. What TermoCam should not copy

* In-memory `job_statuses` as authoritative state.
* FastAPI `BackgroundTasks` as a production queue.
* Daemon threads for uploads or solver work.
* Filesystem report existence as proof of job success.
* Variable `GET /jobs/{id}` response shapes.
* Sibling-repository path assumptions and CLI subprocess coupling.
* Unauthenticated destructive/session routes.
* Wildcard CORS.
* Full upload buffering and extension-only validation.
* Unsafe ZIP `extractall`.
* Raw user IDs in filesystem joins.
* Mock OCR represented as a normal result.
* Hard-coded localhost/server URLs.
* Broad exception catches that print and continue without durable error state.
* One giant document job that must repeat all page OCR after one failure.
* Documentation that references modules absent from the repository.

## 17. Recommended TermoCam implementation plan

### Phase 1 — Server job database

* Files to add/change:
  * `server/db.py`, `server/models.py`, `server/schemas.py`, `server/repositories/`, Alembic configuration/migrations.
  * Split `server/app.py` into routers/services.
* Data models: documents, document_pages, jobs, job_steps, artifacts, events.
* Endpoints: stable create/get job and document routes with idempotency.
* State machine: `QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED`, `RETRY_WAIT`, `CANCEL_REQUESTED`, `CANCELLED`.
* Tests: migrations, state transitions, concurrent updates, idempotency, artifact containment, restart reconciliation.

### Phase 2 — Background queue

* Queue choice: PostgreSQL-backed worker first; SQLite single-worker only for a strictly single-host MVP.
* Worker process: `python -m server.worker`, separate from Uvicorn.
* Retry/timeouts: leases, heartbeat, exponential backoff with jitter, max attempts by job type, per-step deadlines.
* Recovery: startup reclaim of expired leases.
* Tests: API crash, worker crash, lease expiry, duplicate delivery, timeout, queue/database outage.

### Phase 3 — Multi-page sessions

* Endpoints: create document, upload/replace/reorder page, finish document.
* Storage: `documents/<document_id>/pages/<page_id>/...` through artifact service, not direct route paths.
* Page quality: capture metrics immediately; OCR and post-OCR quality asynchronously; explicit accepted/rejected result.
* Processing: per-page jobs followed by document-finalize/solver job.
* Tests: ordering, replacement, duplicate page number, finish validation, mixed accepted/rejected pages, multi-page PDF.

### Phase 4 — Audio feedback events

* Event schema: persisted sequence, event type, severity, message key, spoken text, context IDs, dedupe key.
* API: polling with `after_sequence`, plus SSE when network conditions permit.
* Pi A usage: local phrase catalog and local TTS for operational prompts; cache solver audio.
* Tests: ordering, dedupe, reconnect/resume, localization fallback, offline queue, stale-event expiry.

### Phase 5 — Solver integration

* Request schema: versioned document/pages/questions contract from section 12.
* Response schema: solver job, answers, citations, confidence, model metadata, audio artifact references.
* Retry/reprocess: idempotency key; modes `shorter`, `standard`, `detailed`; solver job linked to source document version.
* Transport: authenticated HTTP API or durable message bus, never a daemon subprocess.
* Tests: QCM/open questions, partial answers, timeout, invalid schema, low OCR confidence, citations, callback replay, TTS-only retry.

## 18. Proposed TermoCam API

All examples use UUID/ULID-like opaque IDs. Every mutation accepts `Idempotency-Key`.

### `POST /documents`

Request:

```json
{"course":"GFN252","language":"fr","title":"Exam scan"}
```

Response `201`:

```json
{"document_id":"doc_123","status":"CAPTURING","next_page_number":1}
```

### `POST /documents/{document_id}/pages`

Request: multipart `file`, plus JSON metadata:

```json
{"page_number":1,"capture_mode":"still","replace_page_id":null}
```

Response `202`:

```json
{
  "page_id":"page_1",
  "status":"QUALITY_CHECK_PENDING",
  "quality_job_id":"job_quality_1",
  "events_url":"/documents/doc_123/events"
}
```

### `POST /documents/{document_id}/finish`

Request:

```json
{"expected_page_count":3,"solve":true,"answer_mode":"standard"}
```

Response `202`:

```json
{"document_id":"doc_123","status":"PROCESSING","job_id":"job_doc_1"}
```

### `GET /documents/{document_id}`

Response:

```json
{
  "document_id":"doc_123",
  "status":"PROCESSING",
  "pages":[
    {"page_id":"page_1","page_number":1,"status":"ACCEPTED","quality_score":0.91}
  ],
  "active_job_id":"job_doc_1"
}
```

### `GET /documents/{document_id}/events?after_sequence=41`

Response:

```json
{
  "events":[
    {
      "sequence":42,
      "event_type":"AUDIO_FEEDBACK",
      "severity":"success",
      "message_key":"page_accepted",
      "spoken_text":"Page 2 accepted.",
      "page_number":2
    }
  ],
  "next_after_sequence":42
}
```

### `GET /documents/{document_id}/debug_report.json`

Response:

```json
{"schema_version":"1.0","document_id":"doc_123","status":"DONE","pages":[],"jobs":[],"errors":[]}
```

### `POST /jobs`

Request:

```json
{"type":"DOCUMENT_PROCESS","document_id":"doc_123","options":{"solve":true}}
```

Response `202`:

```json
{"job_id":"job_123","status":"QUEUED","attempt":0}
```

### `GET /jobs/{job_id}`

Response:

```json
{
  "job_id":"job_123",
  "document_id":"doc_123",
  "type":"DOCUMENT_PROCESS",
  "status":"RUNNING",
  "progress":0.6,
  "current_step":"OCR",
  "attempt":1,
  "error":null,
  "result_urls":{}
}
```

### `POST /jobs/{job_id}/retry`

Request:

```json
{"from_step":"OCR","reason":"page_replaced"}
```

Response `202`:

```json
{"job_id":"job_124","retry_of":"job_123","status":"QUEUED"}
```

### `POST /jobs/{job_id}/cancel`

Request:

```json
{"reason":"operator_cancelled"}
```

Response `202`:

```json
{"job_id":"job_123","status":"CANCEL_REQUESTED"}
```

### `GET /jobs/{job_id}/result/reconstructed.pdf`

Response: `200 application/pdf`; `409` with shared error schema if not ready.

### `GET /jobs/{job_id}/result/ocr.json`

Response:

```json
{
  "document_id":"doc_123",
  "text":"Page 1...\n\nPage 2...",
  "pages":[
    {"page_number":1,"text":"...","mean_confidence":0.91,"blocks":[]}
  ]
}
```

### `GET /jobs/{job_id}/result/answers.json`

Response:

```json
{
  "solver_job_id":"solve_123",
  "answers":[
    {
      "question_id":"open_01",
      "answer_text":"...",
      "confidence":0.84,
      "citations":[{"page_number":2,"source_id":"page_2","quote":"..."}]
    }
  ]
}
```

### `GET /jobs/{job_id}/result/audio/{question_id}.mp3`

Response: `200 audio/mpeg`; use `ETag` and cache headers. If TTS failed while answers succeeded, return `409 TTS_FAILED` and allow a TTS-only retry.

Recommended additions:

```text
GET  /health/live
GET  /health/ready
GET  /documents/{document_id}/events/stream
PUT  /documents/{document_id}/pages/{page_id}
DELETE /documents/{document_id}/pages/{page_id}
POST /documents/{document_id}/pages/reorder
POST /jobs/{job_id}/reprocess
```

## 19. Final recommendation

* How mature is this repo as a solver backend?
  * It is not a solver backend. It is an early scanner/reconstruction prototype with an unverified external solver bridge.
  * Scanner CV/OCR concepts are useful, but server orchestration is not production-grade.
  * Current checkout has blocking defects and missing modules, so “already works” could not be verified.
* Is it stronger than TermoCam in server architecture?
  * No: it is TermoCam, and it lacks the requested durable server features.
* Exact parts to integrate:
  * Engine-neutral OCR JSON.
  * Capture/page quality metrics.
  * Directional feedback message keys.
  * Stitching fallback hierarchy.
  * Per-job artifact grouping and visual debug artifacts.
  * OCR confidence gate before solver dispatch.
* Minimum integration path:
  1. Fix repository completeness and existing still/status defects.
  2. Add database-backed jobs and a separate worker.
  3. Persist page quality and events.
  4. Replace the solver bridge with one authenticated `/v1/solve-jobs` JSON call.
  5. Persist returned answers and expose `answers.json`.
* Best long-term integration path:
  * TermoCam owns capture, document/page lifecycle, OCR artifacts, operational events, and user-facing job state.
  * Solver owns question interpretation, RAG/LLM reasoning, citations, answer confidence, and answer TTS.
  * Connect them through a versioned, idempotent, authenticated asynchronous contract with independent durable job IDs.
  * Do not merge the codebases or share filesystem paths.

## 20. Commands run

Commands are listed in execution order. Combined shell invocations are shown as run.

```bash
find . -maxdepth 4 -type f | sort
ls -la
find . -maxdepth 4 \( -iname '*requirements*.txt' -o -name pyproject.toml -o -name package.json -o -name 'docker-compose*.yml' -o -name .env.example -o -name 'README*' \) -print
sed -n '1,260p' README.md
sed -n '1,280p' docs/architecture.md
sed -n '1,320p' docs/api.md
printf '%s\n' '--- server requirements ---'; sed -n '1,240p' server/requirements-server.txt; printf '%s\n' '--- pi requirements ---'; sed -n '1,240p' pi/requirements-pi.txt; printf '%s\n' '--- git status ---'; git status --short
wc -l server/*.py server/tests/*.py pi/*.py termocam/*.py termocam/pi/*.py docs/*.md pi/config.example.yaml .gitignore
rg -n "@(app|router)\.(get|post|put|patch|delete)|FastAPI|Flask|BackgroundTasks|asyncio|thread|queue|celery|redis|sql|sqlite|database|job|retry|timeout|recover|cancel" server pi termocam -g '*.py'
rg -n "os\.getenv|os\.environ|environ\[|load_dotenv|API_KEY|APP_ID|APP_KEY|TOKEN|SECRET|PASSWORD|URL|CORS|auth|Authorization" . -g '*.py' -g '*.md' -g '*.yaml' -g '*.txt' -g '!INTEGRATION_HANDOFF_FOR_TERMOCAM.md'
rg -n "logging|logger|print\(|debug_report|confidence|sharp|blur|quality|warning|error|tts|audio|mp3|solver|rag|llm|question|answer|citation" server pi termocam -g '*.py'
sed -n '1,340p' server/app.py
sed -n '1,280p' server/process_sweep.py; sed -n '1,180p' server/process_still.py
sed -n '1,300p' server/solver_bridge.py; sed -n '1,280p' server/ocr.py
sed -n '1,180p' server/debug_report.py; sed -n '1,180p' server/capture_quality.py; sed -n '1,140p' server/camera_feedback.py; sed -n '1,340p' server/page_detector.py
find . -type f -not -path './.git/*' | sort
sed -n '1,340p' server/tests/test_pipeline.py
sed -n '1,680p' pi/live_camera_server.py
grep -RInE '@(app|router)\.(get|post|put|patch|delete)|FastAPI|Flask|BackgroundTasks|asyncio|thread|queue|celery|redis|sql|sqlite|database|retry|timeout|recover|cancel' server pi termocam --include='*.py'
grep -RInE 'os\.getenv|os\.environ|environ\[|load_dotenv|API_KEY|APP_ID|APP_KEY|TOKEN|SECRET|PASSWORD|AUTO_SOLVE|MIN_OCR|SOLVER_|USE_CO' . --exclude-dir=.git --exclude='INTEGRATION_HANDOFF_FOR_TERMOCAM.md'
grep -RInE 'logging|logger|debug_report|confidence|sharpness|blur|quality|warning|tts|audio|mp3|solver|rag|llm|question|answer|citation' server pi termocam --include='*.py'
sed -n '280,560p' docs/api.md; printf '%s\n' '--- config ---'; sed -n '1,160p' pi/config.example.yaml; printf '%s\n' '--- gitignore ---'; sed -n '1,120p' .gitignore; printf '%s\n' '--- config.py ---'; sed -n '1,120p' termocam/config.py
git ls-tree -r --name-only HEAD | sort; git log --oneline --decorate -10
git ls-tree -r --name-only 5e29936 | sort; git show --stat --oneline 5e29936; git show --stat --oneline 2df4983
sed -n '1,280p' server/rectify.py; sed -n '1,260p' server/stitch.py; sed -n '1,120p' server/enhance.py
grep -RInE 'audio|tts|mp3|speech|RAG|rag|LLM|llm|citation|question|answer|qcm|QCM|reprocess|shorter|detailed' . --exclude-dir=.git --exclude='INTEGRATION_HANDOFF_FOR_TERMOCAM.md'
find . -maxdepth 3 -type d | sort; find server -maxdepth 3 -type f -path '*/data/*' -print; find pi -maxdepth 4 -type f -path '*/data/*' -print
pytest -q
python -m compileall -q server pi termocam
python -c 'import server.app'
git grep -n -E 'class .*Base|BaseModel|CREATE TABLE|SQLAlchemy|sqlmodel|sqlite|postgres|redis|celery|rq|dramatiq|BackgroundTasks|threading.Thread' HEAD -- '*.py'
grep -RInE 'uvicorn|pip install|python .*live_camera|flask run|docker|compose|run locally|localhost' README.md docs pi server --exclude='INTEGRATION_HANDOFF_FOR_TERMOCAM.md'
nl -ba server/app.py | sed -n '1,290p'; nl -ba server/process_sweep.py | sed -n '1,230p'; nl -ba server/solver_bridge.py | sed -n '1,270p'
nl -ba server/process_still.py | sed -n '1,150p'; nl -ba server/debug_report.py | sed -n '1,120p'; nl -ba server/ocr.py | sed -n '1,250p'; nl -ba server/capture_quality.py | sed -n '1,130p'; nl -ba server/camera_feedback.py | sed -n '1,110p'
nl -ba pi/live_camera_server.py | sed -n '1,640p'; nl -ba server/tests/test_pipeline.py | sed -n '1,310p'
grep -n '^## ' INTEGRATION_HANDOFF_FOR_TERMOCAM.md; wc -l -w INTEGRATION_HANDOFF_FOR_TERMOCAM.md
git status --short; git diff --stat; git diff --check
grep -nE '(MATHPIX_APP_KEY|MATHPIX_APP_ID|MATHPIX_API_ID|TOKEN|PASSWORD|SECRET)[[:space:]]*=[[:space:]]*[^<[:space:]]+' INTEGRATION_HANDOFF_FOR_TERMOCAM.md || true
sed -n '1,80p' INTEGRATION_HANDOFF_FOR_TERMOCAM.md; tail -80 INTEGRATION_HANDOFF_FOR_TERMOCAM.md
```

Errors and inspection limits:

* All three `rg` commands failed with `/bin/bash: rg: command not found` and exit code 127. Equivalent `grep` searches were run.
* `pytest -q` failed during collection because NumPy is not installed in the environment.
* `python -c 'import server.app'` failed because FastAPI is not installed in the environment.
* `python -m compileall -q server pi termocam` succeeded, but compilation does not validate imports or runtime call signatures.
* Dependencies were not installed because the task requested read-only inspection and network/package installation was unnecessary for the static findings.
* The external `math_solver_backend`, co-scientist repository, real `.env`, local `config.yaml`, runtime data directories, and missing `pi/capture` package were unavailable for inspection.
* No code was modified. This report is the only file created.
