# REST API Specification & Payload Index

This document outlines the REST APIs for both the Raspberry Pi edge capture device and the heavy reconstruction processing server. It contains complete schemas, request/response structures, error responses, and consumption examples.

---

## Table of Contents
1. [Endpoint Overview Matrix](#1-endpoint-overview-matrix)
2. [Pi Edge Capture API Reference](#2-pi-edge-capture-api-reference)
   * [GET /](#get-)
   * [GET /health](#get-health)
   * [GET /stream](#get-stream)
   * [GET /photo](#get-photo)
   * [GET /calibrate](#get-calibrate)
   * [POST /sweep/start](#post-sweepstart)
   * [POST /sweep/stop](#post-sweepstop)
   * [GET /sweep/status](#get-sweepstatus)
   * [GET /sweep/sessions](#get-sweepsessions)
   * [GET /sweep/:session_id/manifest](#get-sweepsession_idmanifest)
   * [GET /sweep/:session_id/zip](#get-sweepsession_idzip)
   * [POST /sweep/:session_id/upload](#post-sweepsession_idupload)
   * [DELETE /sweep/:session_id](#delete-sweepsession_id)
3. [Reconstruction Server API Reference](#3-reconstruction-server-api-reference)
   * [GET /health](#get-health-1)
   * [POST /process-sweep](#post-process-sweep)
   * [GET /jobs/:job_id](#get-jobsjob_id)
   * [GET /jobs/:job_id/result/reconstructed.jpg](#get-jobsjob_idresultreconstructedjpg)
   * [GET /jobs/:job_id/result/reconstructed.pdf](#get-jobsjob_idresultreconstructedpdf)
   * [GET /jobs/:job_id/result/ocr.json](#get-jobsjob_idresultocrjson)
   * [GET /jobs/:job_id/result/debug_report.json](#get-jobsjob_idresultdebug_reportjson)
4. [Client Consumption & Mock Integration Code](#4-client-consumption--mock-integration-code)

---

## 1. Endpoint Overview Matrix

### Pi Edge Device (`192.168.1.153:5000`)
| Method | Route | Description | State Lock Acquired |
| :--- | :--- | :--- | :--- |
| `GET` | `/` | Serves dashboard UI panel | No |
| `GET` | `/health` | CPU temp & disk metrics | No |
| `GET` | `/stream` | MJPEG alignment stream | Yes (`STREAMING`) |
| `GET` | `/photo` | Legacy warped still photo | Yes (`CAPTURING_STILL`) |
| `GET` | `/calibrate` | Legacy unwarped calibration photo | Yes (`CAPTURING_STILL`) |
| `POST` | `/sweep/start` | Starts a sweep capture session | Yes (`SWEEP_RUNNING`) |
| `POST` | `/sweep/stop` | Stops the active sweep session | Release lock |
| `GET` | `/sweep/status` | Polling endpoint for active frames count | No |
| `GET` | `/sweep/sessions` | Lists stored historical sessions | No |
| `GET` | `/sweep/:sid/manifest`| Returns session manifest.json | No |
| `GET` | `/sweep/:sid/zip` | Downloads compressed ZIP of session | No |
| `POST` | `/sweep/:sid/upload` | Zips & POSTs session to backend server | No |
| `DELETE`| `/sweep/:sid` | Purges session folders from disk | No |

### Reconstruction Server (`192.168.1.151:8000`)
| Method | Route | Description | Input Format |
| :--- | :--- | :--- | :--- |
| `GET` | `/health` | Server availability check | None |
| `POST` | `/process-sweep` | Ingests ZIP and triggers reconstruction | Multipart form-data |
| `GET` | `/jobs/:job_id` | Status & results payload | None |
| `GET` | `/jobs/:job_id/result/reconstructed.jpg` | Serves final rectified A4 composite | None |
| `GET` | `/jobs/:job_id/result/reconstructed.pdf` | Serves final document PDF | None |
| `GET` | `/jobs/:job_id/result/ocr.json` | Serves text bounding box results | None |
| `GET` | `/jobs/:job_id/result/debug_report.json` | Serves audit debug JSON | None |

---

## 2. Pi Edge Capture API Reference

### GET /
Serves the control dashboard HTML file containing javascript polling scripts.
*   **Headers:** `Accept: text/html`
*   **Response:** `200 OK` (text/html content)

---

### GET /health
Returns hardware diagnostics and current device lock state.
*   **Headers:** `Accept: application/json`
*   **Response `200 OK` Schema:**
    ```json
    {
      "status": "ok | warning",
      "system_temp_c": 52.3,
      "free_disk_mb": 450,
      "state": "IDLE | STREAMING | SWEEP_RUNNING | CAPTURING_STILL | ERROR"
    }
    ```

---

### GET /stream
Initiates an boundary-defined Motion JPEG (MJPEG) stream.
*   **Headers:** `Accept: multipart/x-mixed-replace`
*   **Response `200 OK`:** Continuous binary payload stream separated by `--frame` boundary segments.
*   **Response `409 Conflict`:** Returned if the camera is currently occupied by a sweep or still capture.
    ```json
    "Conflict: Camera is busy running a sweep session."
    ```

---

### GET /photo
Autofocuses, locks lens, captures a high-resolution still image, and overlays warp matrix mappings.
*   **Headers:** `Accept: image/jpeg`
*   **Response `200 OK`:** Binary image data (`image/jpeg`).
*   **Response `409 Conflict`:** Camera device busy.
*   **Response `500 Internal Error`:** Capture failure.
    ```json
    {
      "status": "error",
      "message": "Failed to capture still JPEG from sensor."
    }
    ```

---

### GET /calibrate
Autofocuses once and captures a completely unwarped still frame.
*   **Headers:** `Accept: image/jpeg`
*   **Response `200 OK`:** Binary image data (`image/jpeg`).

---

### POST /sweep/start
Initializes a new sweep session directory and starts a background frame capture thread.
*   **Headers:** `Content-Type: application/json`, `Accept: application/json`
*   **Request Body Parameters:**
    | Property | Type | Required | Default | Description |
    | :--- | :--- | :--- | :--- | :--- |
    | `interval_ms` | Integer | No | `150` | Milliseconds between frame evaluations |
    | `max_frames` | Integer | No | `120` | Maximum frame budget to save |
    | `sharpness_threshold` | Float | No | `120.0` | Laplacian score threshold (reject if lower) |
    | `min_frame_difference` | Float | No | `8.0` | Downsampled frame diff threshold |
    | `jpeg_quality` | Integer | No | `90` | Image compression quality (50-100) |
    | `resolution` | Array | No | `[2304, 1296]`| Width and height dimensions |
    | `upload_after_capture` | Boolean | No | `false` | Trigger server upload on sweep stop |
    | `server_url` | String | No | `""` | Processing server API route |

*   **Request Body Example:**
    ```json
    {
      "interval_ms": 150,
      "max_frames": 120,
      "sharpness_threshold": 120.0,
      "min_frame_difference": 8.0,
      "resolution": [2304, 1296],
      "jpeg_quality": 90,
      "upload_after_capture": true,
      "server_url": "http://192.168.1.151:8000/process-sweep"
    }
    ```
*   **Response `200 OK` Example:**
    ```json
    {
      "session_id": "sess_1773019312",
      "status": "running"
    }
    ```
*   **Response `400 Bad Request`:** Returned if temperature exceeds limit ($>80^\circ\text{C}$) or disk is full ($<50\text{MB}$).
*   **Response `409 Conflict`:** Camera busy streaming or capturing a still.

---

### POST /sweep/stop
Stops the active capture loop and releases camera locks.
*   **Headers:** `Accept: application/json`
*   **Response `200 OK` Example:**
    ```json
    {
      "session_id": "sess_1773019312",
      "status": "stopped",
      "accepted_frames": 42,
      "rejected_frames": 17
    }
    ```

---

### GET /sweep/status
Returns metrics from the current or most recent sweep session.
*   **Headers:** `Accept: application/json`
*   **Response `200 OK` Example:**
    ```json
    {
      "status": "running | idle | stopping | error",
      "current_session_id": "sess_1773019312",
      "accepted_frames": 32,
      "rejected_frames": 8,
      "last_error": null
    }
    ```

---

### GET /sweep/sessions
Returns a list of completed sweep sessions stored on the local storage partition.
*   **Headers:** `Accept: application/json`
*   **Response `200 OK` Example:**
    ```json
    {
      "sessions": [
        {
          "session_id": "sess_1773019312",
          "created_at": "2026-06-06T00:10:00Z",
          "accepted_frames": 42,
          "rejected_frames": 17
        }
      ]
    }
    ```

---

### GET /sweep/:session_id/manifest
Returns the raw contents of `manifest.json` for a specific session.
*   **Headers:** `Accept: application/json`
*   **Response `200 OK` Schema matches manifest specifications.** (See architecture documentation).
*   **Response `404 Not Found`:** If session ID is not recognized.

---

### GET /sweep/:session_id/zip
Downloads a ZIP archive container of the specified session.
*   **Headers:** `Accept: application/zip`
*   **Response `200 OK`:** ZIP file binary content.

---

### POST /sweep/:session_id/upload
Triggers the edge device to package the session folder into a ZIP container and upload it to the reconstruction server.
*   **Headers:** `Accept: application/json`
*   **Response `200 OK` Example:**
    ```json
    {
      "status": "success",
      "http_code": 200,
      "response": {
        "job_id": "job_1773019550_abc123",
        "status": "pending",
        "message": "Upload successful. Session added to queue."
      }
    }
    ```
*   **Response `400 Bad Request`:** Missing server URL configuration.
*   **Response `500 Internal Error`:** Network failure or ZIP generation error.

---

### DELETE /sweep/:session_id
Purges the directory and temporary archives associated with a session ID to clear disk space.
*   **Headers:** `Accept: application/json`
*   **Response `200 OK` Example:**
    ```json
    {
      "status": "success",
      "message": "Deleted session sess_1773019312"
    }
    ```

---

## 3. Reconstruction Server API Reference

### GET /health
Indicates server availability and response state.
*   **Response `200 OK`:**
    ```json
    {
      "status": "ok",
      "uptime_sec": 12504.2
    }
    ```

---

### POST /process-sweep
Ingests an uploaded ZIP archive and schedules processing in a background worker task.
*   **Request Type:** Multipart form-data
*   **Form Fields:**
    *   `file`: Binary file upload (`application/zip`)
*   **Response `202 Accepted` (Returned as 200):**
    ```json
    {
      "job_id": "job_1773019550_abc123",
      "status": "pending",
      "message": "Upload successful. Session added to queue."
    }
    ```
*   **Response `400 Bad Request`:** File is not a ZIP.

---

### GET /jobs/:job_id
Returns the status of a scheduled reconstruction job. If the job is complete, this endpoint returns the full debug report.
*   **Response `200 OK` (Job running/pending):**
    ```json
    {
      "job_id": "job_1773019550_abc123",
      "status": "pending | running | failed",
      "message": "Job is currently pending."
    }
    ```
*   **Response `200 OK` (Job completed):**
    Returns full [debug_report.json](#get-jobsjob_idresultdebug_reportjson).
*   **Response `404 Not Found`:** Job ID is unknown.

---

### GET /jobs/:job_id/result/reconstructed.jpg
Serves the final stitched and rectified A4 composite image.
*   **Response `200 OK`:** Binary image data (`image/jpeg`).
*   **Response `404 Not Found`:** Output not ready or job failed.

---

### GET /jobs/:job_id/result/reconstructed.pdf
Serves the final compiled A4 document PDF.
*   **Response `200 OK`:** Binary file data (`application/pdf`).

---

### GET /jobs/:job_id/result/ocr.json
Serves the structured OCR text array transcript.
*   **Response `200 OK`:** JSON document conforming to OCR schema.
    ```json
    {
      "text": "Extracted text content\nLine two",
      "lines": [
        {
          "text": "Extracted text content",
          "confidence": 0.985,
          "bbox": [[100, 100], [400, 100], [400, 140], [100, 140]]
        }
      ],
      "fields": {}
    }
    ```

---

### GET /jobs/:job_id/result/debug_report.json
Serves the audit report detailing frames count, quality statistics, and stitching performance metrics.
*   **Response `200 OK` Schema:**
    ```json
    {
      "job_id": "job_1773019550_abc123",
      "session_id": "sess_1773019312",
      "input": {
        "frame_count": 42,
        "resolution": [2304, 1296]
      },
      "stitching": {
        "method_attempted": ["SCANS", "PANORAMA", "CUSTOM_FEATURE"],
        "method_used": "SCANS",
        "status": "success",
        "opencv_status_code": 0,
        "warnings": []
      },
      "quality": {
        "mean_sharpness": 182.4,
        "min_sharpness": 121.8,
        "rejected_server_side": 0
      },
      "ocr": {
        "engine": "PaddleOCR",
        "line_count": 1,
        "mean_confidence": 0.98
      },
      "outputs": {
        "reconstructed_image": "reconstructed.jpg",
        "pdf": "reconstructed.pdf",
        "ocr_json": "ocr.json"
      }
    }
    ```

---

## 4. Client Consumption & Mock Integration Code

Here are examples of how to consume these endpoints.

### 4.1 Shell Command (curl)

**Trigger sweep start on Pi:**
```bash
curl -X POST http://192.168.1.153:5000/sweep/start \
     -H "Content-Type: application/json" \
     -d '{"interval_ms": 150, "max_frames": 100, "upload_after_capture": true, "server_url": "http://192.168.1.151:8000/process-sweep"}'
```

**Query active status:**
```bash
curl http://192.168.1.153:5000/sweep/status
```

**Stop active sweep:**
```bash
curl -X POST http://192.168.1.153:5000/sweep/stop
```

**Manually upload zip from client:**
```bash
curl -X POST http://192.168.1.153:5000/sweep/sess_1773019312/upload
```

**Check server job status:**
```bash
curl http://192.168.1.151:8000/jobs/job_1773019550_abc123
```

---

### 4.2 Python Script Consumption & Orchestration

The following code illustrates how to orchestrate a sweep capture and fetch reconstruction results programmatically:

```python
import time
import requests

PI_IP = "192.168.1.153"
SERVER_IP = "192.168.1.151"

def run_document_scan():
    # 1. Start session
    print("Initiating Sweep Session...")
    start_res = requests.post(
        f"http://{PI_IP}:5000/sweep/start",
        json={
            "interval_ms": 150,
            "max_frames": 150,
            "sharpness_threshold": 120.0,
            "upload_after_capture": False # We will handle upload manually
        }
    )
    if start_res.status_code != 200:
        print(f"Failed to start: {start_res.text}")
        return
        
    session_id = start_res.json()["session_id"]
    print(f"Active Session: {session_id}")
    
    # 2. Simulate sweep wait (User sweeps camera)
    print("Capturing sweep data... (Waiting 10 seconds)")
    for _ in range(10):
        time.sleep(1)
        status = requests.get(f"http://{PI_IP}:5000/sweep/status").json()
        print(f"  Frames captured: {status['accepted_frames']} (Rejected: {status['rejected_frames']})")
        
    # 3. Stop session
    print("Stopping session...")
    stop_res = requests.post(f"http://{PI_IP}:5000/sweep/stop")
    print(f"Stopped: {stop_res.json()}")
    
    # 4. Trigger upload to server
    print("Uploading ZIP to processing server...")
    upload_res = requests.post(f"http://{PI_IP}:5000/sweep/{session_id}/upload")
    upload_data = upload_res.json()
    
    if upload_data["status"] != "success":
        print(f"Upload failed: {upload_data['message']}")
        return
        
    job_id = upload_data["response"]["job_id"]
    print(f"Server Job ID: {job_id}")
    
    # 5. Poll server job status
    print("Polling server reconstruction pipeline...")
    while True:
        job_status = requests.get(f"http://{SERVER_IP}:8000/jobs/{job_id}").json()
        status = job_status.get("status")
        
        if status in ("completed", "success"):
            print("Reconstruction complete!")
            break
        elif status == "failed":
            print("Job pipeline failed. Check server logs.")
            return
        else:
            print(f"  Current status: {status}... sleeping 2s")
            time.sleep(2)
            
    # 6. Fetch results
    print("Downloading PDF...")
    pdf_res = requests.get(f"http://{SERVER_IP}:8000/jobs/{job_id}/result/reconstructed.pdf")
    with open("scanned_document.pdf", "wb") as f:
        f.write(pdf_res.content)
        
    ocr_res = requests.get(f"http://{SERVER_IP}:8000/jobs/{job_id}/result/ocr.json").json()
    print("OCR Text preview:")
    print(ocr_res["text"][:300])

if __name__ == "__main__":
    run_document_scan()
```
