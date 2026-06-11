# Durable Server Orchestration

TermoCam now stores documents, pages, jobs, steps, artifacts, events, questions, and answers in SQLAlchemy-managed tables. CPU processing runs in a separate database-backed worker.

## Install

```bash
python -m pip install -r server/requirements-server.txt
python -m pip install -r pi/requirements-pi.txt
```

Copy `.env.example` to a local `.env` and set secrets outside Git.

## Database

Default:

```text
DATABASE_URL=sqlite:///server/data/termocam.db
```

Apply migrations:

```bash
python -m alembic upgrade head
```

Create a migration after model changes:

```bash
python -m alembic revision --autogenerate -m "describe change"
```

The models use portable string states, JSON serialized as text, and standard SQLAlchemy relationships so PostgreSQL can replace SQLite through `DATABASE_URL`.

## Run

API:

```bash
python -m uvicorn server.app:app --host 0.0.0.0 --port 8000
```

Worker:

```bash
python -m server.worker
```

Run one worker with SQLite. PostgreSQL workers use row locking with `FOR UPDATE SKIP LOCKED`.

## Basic API

Create a document:

```bash
curl -X POST http://127.0.0.1:8000/documents \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: exam-001' \
  -d '{"course":"GFN252","language":"fr","title":"Exam scan"}'
```

Upload a page:

```bash
curl -X POST http://127.0.0.1:8000/documents/doc_ID/pages \
  -H 'Idempotency-Key: exam-001-page-1' \
  -F 'file=@page1.jpg;type=image/jpeg' \
  -F 'metadata_json={"page_number":1,"capture_mode":"still","replace_page_id":null}'
```

Finish:

```bash
curl -X POST http://127.0.0.1:8000/documents/doc_ID/finish \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: exam-001-finish-v1' \
  -d '{"expected_page_count":1,"solve":false,"answer_mode":"standard"}'
```

Poll:

```bash
curl http://127.0.0.1:8000/jobs/job_ID
curl 'http://127.0.0.1:8000/documents/doc_ID/events?after_sequence=0'
```

When `TERMOCAM_SERVICE_TOKEN` is set, add:

```text
Authorization: Bearer <token>
```

## Worker Lifecycle

The worker:

1. Reclaims expired `RUNNING` leases at startup.
2. Claims `QUEUED` or due `RETRY_WAIT` work.
3. Persists attempt, step, progress, heartbeat, and lease expiry.
4. Executes the existing still/sweep pipeline lazily.
5. Registers generated files as immutable artifacts.
6. Writes a stable orchestration debug report.
7. Retries recoverable failures with exponential backoff and jitter.

Queued jobs cancel immediately. Running jobs become `CANCEL_REQUESTED` and are finalized as cancelled after the current non-interruptible CV call returns.

## Backward Compatibility

These routes remain:

```text
POST /process-still
POST /process-sweep
GET  /jobs/{job_id}
GET  /jobs/{job_id}/result/reconstructed.jpg
GET  /jobs/{job_id}/result/reconstructed.pdf
GET  /jobs/{job_id}/result/ocr.json
GET  /jobs/{job_id}/result/debug_report.json
```

Legacy uploads now create durable jobs. Their response retains `status: pending` and adds `durable_status: QUEUED`. Job reads always use the new stable shape. Result routes query artifact records first and then validated legacy directories.

## Solver Contract

`SOLVER_DISPATCH` sends the versioned `/v1/solve-jobs` JSON contract when `SOLVER_BASE_URL` is configured. The callback route stores answer text, choices, confidence, citations, model metadata, answer JSON, and audio artifact references.

This repository does not implement the external RAG/LLM/TTS engine. If unavailable, only the solver job enters recoverable failure; OCR and PDF artifacts remain available.

## Current Limits

* SQLite supports one worker. Use PostgreSQL before horizontal worker scaling.
* Existing OpenCV pipeline functions are non-interruptible; cancellation is cooperative between stages.
* Heartbeats run during long handlers, but an abrupt process kill is recovered only after lease expiry.
* Page quality thresholds are conservative defaults and need calibration with real TermoCam captures.
* SSE is a simple polling stream without a broker; normal event polling is the supported Raspberry Pi path.
* Server-generated MP3 requires an external solver/TTS callback. Operational prompts are event text for local Pi speech.
