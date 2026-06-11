# TermoCam Camera Scanner

TermoCam is a two-part document scanning system:

- A Raspberry Pi capture app for live preview, page detection, and sweep capture.
- A reconstruction server that stitches frames, rectifies the page, runs OCR, and publishes the final outputs.

The design keeps the Pi lightweight and moves the heavy computer-vision work to the server.

## What It Does

- Live MJPEG preview for alignment
- Page detection on preview frames with capture feedback
- Single still capture and processing
- Sweep capture sessions with frame filtering, manifest generation, and ZIP upload
- Server-side stitching of captured frames
- Rectification to A4 layout
- OCR with Mathpix, PaddleOCR, or Tesseract fallback
- PDF generation and debug report output
- Optional forwarding of OCR output to a solver backend

## Main Components

### Raspberry Pi App

`pi/live_camera_server.py` runs the Flask control server and manages:

- camera state locking
- live preview stream
- still photo capture
- sweep start/stop flow
- session archiving and upload

### Reconstruction Server

`server/app.py` runs the FastAPI service and exposes:

- `/detect-page-preview`
- `/process-still`
- `/process-sweep`
- `/jobs/{job_id}`
- result download endpoints for JPEG, PDF, OCR JSON, and debug reports

The server pipeline is implemented across:

- `server/process_sweep.py`
- `server/stitch.py`
- `server/rectify.py`
- `server/enhance.py`
- `server/ocr.py`
- `server/debug_report.py`

## Processing Flow

1. The Pi opens a preview stream so the user can align the document.
2. Preview frames go to the server for page detection and feedback.
3. The user starts a sweep or captures a still image.
4. The Pi stores accepted frames locally and creates a ZIP archive.
5. The ZIP is uploaded to the reconstruction server.
6. The server extracts frames, stitches them, rectifies the page, enhances the image, and runs OCR.
7. The server writes:
   - `reconstructed.jpg`
   - `reconstructed.pdf`
   - `ocr.json`
   - `debug_report.json`

## Output Artifacts

Each completed job is stored under `server/data/jobs/<job_id>/`.

### Generated files

- `reconstructed.jpg` - final cleaned page image
- `reconstructed.pdf` - PDF export of the reconstructed page
- `ocr.json` - structured OCR output with text and bounding boxes
- `debug_report.json` - pipeline status, quality metrics, and warnings

## OCR Behavior

The OCR pipeline tries engines in this order:

1. Mathpix, if `MATHPIX_APP_ID` and `MATHPIX_APP_KEY` are present
2. PaddleOCR, if installed
3. Tesseract, if installed
4. A mock fallback response when no OCR engine is available

## Configuration Notes

- The Pi reads camera and limit settings from `pi/config.yaml` or `pi/config.example.yaml`
- The server loads `.env` from the repository root for OCR credentials
- `AUTO_SOLVE_AFTER_OCR=true` enables optional forwarding to the solver bridge

## Repository Layout

- `pi/` - Raspberry Pi capture service and camera session logic
- `server/` - FastAPI reconstruction backend
- `docs/` - architecture, calibration, hardware, and API notes
- `warp_points.json` - optional capture warp calibration
- `yolov8n-seg.pt` - segmentation model used by the page detection pipeline

## API Summary

### Pi

- `GET /` - control dashboard
- `GET /health` - system health and state
- `GET /stream` - live preview stream
- `GET /photo` - legacy single-frame capture
- `GET /calibrate` - calibration capture
- `POST /sweep/start` - start sweep session
- `POST /sweep/stop` - stop sweep session
- `GET /sweep/status` - session status
- `GET /sweep/sessions` - saved sessions
- `GET /sweep/<session_id>/zip` - download session archive
- `POST /sweep/<session_id>/upload` - upload session archive
- `DELETE /sweep/<session_id>` - delete session

### Server

- `GET /health` - server status
- `POST /detect-page-preview` - page detection on preview frame
- `POST /process-still` - process a single image
- `POST /process-sweep` - process a sweep ZIP
- `GET /jobs/<job_id>` - job status
- `GET /jobs/<job_id>/result/reconstructed.jpg` - reconstructed image
- `GET /jobs/<job_id>/result/reconstructed.pdf` - reconstructed PDF
- `GET /jobs/<job_id>/result/ocr.json` - OCR output
- `GET /jobs/<job_id>/result/debug_report.json` - debug report

## Setup

Install the Pi and server dependencies separately:

- `pi/requirements-pi.txt`
- `server/requirements-server.txt`

Then run the Pi app on the capture device and the FastAPI server on the processing host.

