# TermoCam Pi Document Flow

This flow uses the new document/page API instead of the legacy single-upload path.
It preserves the existing camera preview, still capture, sweep capture, and legacy upload routes.

## Environment variables

Set these on the Pi before starting the Flask app:

```bash
export TERMOCAM_SERVER_BASE_URL=http://localhost:8000
export TERMOCAM_DEVICE_TOKEN=
export TERMOCAM_DEFAULT_COURSE=
export TERMOCAM_DEFAULT_LANGUAGE=fr
export TERMOCAM_REQUEST_TIMEOUT_SECONDS=60
export TERMOCAM_PI_STATE_PATH=pi/data/document_state.json
```

Optional Pi-server local endpoint override:

```bash
export TERMOCAM_PI_BASE_URL=http://127.0.0.1:5000
```

## Start the services

```bash
python -m server.worker
python pi/live_camera_server.py
```

The FastAPI server must be running before the Pi can create documents or upload pages.
The Pi persists the active document to `pi/data/document_state.json` by default.
Set `TERMOCAM_PI_STATE_PATH` to use a different writable path.

## New document scan flow

1. Open the Pi dashboard.
2. Start a document with `/document/start`.
3. Capture page stills with `/document/capture-still` or a sweep page with `/document/capture-sweep/start` and `/document/capture-sweep/stop`.
4. Poll `/document/events` for audio feedback messages.
5. Finish the document with `/document/finish`.

### Example curl sequence

```bash
curl -X POST http://pi:5000/document/start \
  -H "Content-Type: application/json" \
  -d '{"course":"GFN252","language":"fr","title":"Exam scan"}'

curl -X POST http://pi:5000/document/capture-still

curl http://pi:5000/document/events

curl -X POST http://pi:5000/document/finish \
  -H "Content-Type: application/json" \
  -d '{"expected_page_count":3,"solve":true,"answer_mode":"standard"}'
```

## Restart recovery

After every successful document action, the Pi atomically writes the active document ID,
page list, next page number, event sequence, status, and update timestamp to its state file.

When the Pi Flask app starts after an app restart or Pi reboot:

1. It loads the local state file.
2. If a document ID is present, it requests `GET /documents/{document_id}` from the server.
3. It refreshes pages, document status, and the next page number from the server.
4. It preserves the persisted `last_event_sequence`, so the next event poll receives missed events.

Check recovery before continuing capture:

```bash
curl http://pi:5000/document/status
```

If `server_reachable` is `false`, the local state is retained with `needs_resync: true`.
Restore server connectivity, then call `/document/resync` or `/document/status`. Do not reset
unless you intend to discard the Pi's local attachment to that document.

If the server reports that the document does not exist, the Pi retains the local state and
reports `DOCUMENT_NOT_FOUND`. Attach a valid document or reset the local state explicitly.

## Attach and resync

Attach the Pi to an existing durable server document:

```bash
curl -X POST http://pi:5000/document/attach \
  -H "Content-Type: application/json" \
  -d '{"document_id":"doc_..."}'
```

Refresh the current local document from the server:

```bash
curl -X POST http://pi:5000/document/resync
```

Attach replaces the current local attachment after the requested server document is found.
Resync keeps the current document ID and `last_event_sequence` while refreshing pages, status,
and the next page number.

## Event polling

Use `GET /document/events` to pull new server events since the last sequence number.
The Pi persists the returned sequence before the next poll and sends received events to
`audio_feedback.handle_audio_events`.

## Reset local state

Reset only after confirming that the local Pi attachment is no longer needed:

```bash
curl -X POST http://pi:5000/document/reset
```

Reset clears the Pi state file and in-memory document state. It does not delete the durable
server document. A server document can be attached again later with `/document/attach`.

The Pi does not queue captured images for later upload. If an upload fails while the server is
offline, the page counter is not advanced; capture and upload the page again after connectivity
is restored.

## Legacy compatibility

The following routes still work and remain available for older workflows:

* `/photo`
* `/process-highres`
* `/sweep/start`
* `/sweep/stop`
* `/sweep/status`
* `/sweep/<session_id>/upload`

The new document mode does not remove or rename those endpoints.
