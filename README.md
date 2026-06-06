# TermoCam Sweep-Based A4 Scanner & Reconstruction System

TermoCam is a distributed, high-performance A4 document digitizer. The system splits the workflow into a lightweight edge capture device (Raspberry Pi Zero 2W + Sony IMX708) and a heavy processing reconstruction backend (Server/Mac/Cloud).

This design ensures fast edge frame rates, low latency, and robust document reconstruction, avoiding memory overflow and thermal limits on the constrained edge hardware.

---

## 1. System Architecture Overview

```
+------------------------------------+          +------------------------------------+
|  Raspberry Pi Zero 2W (Appliance)   |          |  Reconstruction Server (Engine)     |
|  - Controls Sony IMX708 Camera     |          |  - FastAPI Daemon                  |
|  - Fast frame acquisition          |  Upload  |  - OpenCV Image Stitching          |
|  - Blur & motion filtering         |  (ZIP)   |  - Deskew, Rectify & Crop          |
|  - Manifest generation             +--------->+  - PaddleOCR / Tesseract           |
|  - Local temporary staging         |          |  - PDF & JSON compilation          |
+------------------------------------+          +------------------------------------+
```

### 1.1 Why We Partition the System
The Raspberry Pi Zero 2W is constrained to **512 MB LPDDR2 RAM** and a 1 GHz ARM Cortex-A53 processor. Running heavy computer vision packages, homography calculations, and OCR engines directly on the edge causes:
1.  **Out-Of-Memory (OOM) Crashes:** Processing high-resolution images (> 11 Megapixels) instantly consumes all available RAM.
2.  **Thermal Throttling:** Running deep learning libraries pushes the core temperature past $80^\circ\text{C}$, dropping CPU clocks to 600 MHz.
3.  **Low Capture Rates:** Local processing drops frame acquisition rates below 1 frame per second, preventing smooth document sweeps.

**The Solution:** The Pi Zero acts strictly as a **Capture Appliance** to ingest, filter, and stage frames. The server acts as the **Reconstruction Engine** to stitch frames and perform OCR.

---

## 2. Hardware Requirements & Build of Materials (BOM)

To build this setup, prepare the following items:
*   **Raspberry Pi Zero 2W:** Single-board computer.
*   **Sony IMX708 (Camera Module 3):** 11.9 Megapixel sensor with Phase Detection Autofocus (PDAF).
*   **Articulated Mount/Desk Stand:** Keeps the camera rigid and parallel to the desk surface. Hand-held capture will fail stitching.
*   **contrasting desk surface:** A dark desk is necessary to detect white paper boundaries.
*   **CSI Ribbon Cable (15 cm):** Smaller pitch connector designed for the Pi Zero.
*   **Power Supply:** 5.1V 2.5A Micro-USB adapter.

---

## 3. Installation & Setup

### 3.1 Edge Appliance Setup (Raspberry Pi)
1.  Flash **Raspberry Pi OS Lite (64-bit, Bookworm)** using the Raspberry Pi Imager.
2.  Configure camera overlays by editing `/boot/firmware/config.txt`:
    ```ini
    camera_auto_detect=0
    dtoverlay=imx708
    gpu_mem=16
    ```
3.  Increase swap memory to prevent zipping memory allocation crashes:
    ```bash
    sudo dphys-swapfile swapoff
    # Edit /etc/dphys-swapfile and set: CONF_SWAPSIZE=1024
    sudo dphys-swapfile setup
    sudo dphys-swapfile swapon
    ```
4.  Clone this repository to `/home/pi/camera_scanner_termocam`.
5.  Install dependencies:
    ```bash
    cd /home/pi/camera_scanner_termocam/pi
    pip3 install -r requirements-pi.txt
    ```
6.  Setup the autostart background daemon:
    ```bash
    sudo cp /home/pi/camera_scanner_termocam/docs/termocam.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable termocam.service
    sudo systemctl start termocam.service
    ```

### 3.2 Reconstruction Server Setup
1.  Clone this repository to your processing server or Mac.
2.  Install system dependencies (like Tesseract and OpenCV prerequisites):
    *   **macOS (Homebrew):** `brew install tesseract`
    *   **Ubuntu/Debian:** `sudo apt-get install tesseract-ocr libgl1`
3.  Install Python dependencies:
    ```bash
    cd camera_scanner_termocam/server
    pip3 install -r requirements-server.txt
    ```
4.  *(Optional)* Install PaddleOCR and PaddlePaddle for improved text coordinates:
    ```bash
    pip3 install paddleocr paddlepaddle
    ```
5.  Start the FastAPI application daemon:
    ```bash
    uvicorn app:app --host 0.0.0.0 --port 8000
    ```

---

## 4. Operation & Execution Guide

### 4.1 Running a Document Sweep Capture
1.  Place the A4 document on the desk under the camera arm.
2.  Access the web control panel by navigating to `http://<pi-ip-address>:5000/` in your browser.
3.  Click **Start Alignment Stream** to view the live preview and adjust the paper placement.
4.  Configure sweep parameters (Interval, thresholds) and enter the reconstruction server URL:
    `http://<server-ip-address>:8000/process-sweep`
5.  Check **Upload automatically on completion** if you want the Pi to POST the ZIP immediately when stopped.
6.  Click **Start New Sweep** and manually slide the camera over the A4 page.
    > [!IMPORTANT]
    > **Sweep overlap requirement:** Move the camera slowly. To stitch frames successfully, maintain **60% to 80% overlap** between consecutive frames.
7.  Click **Stop Sweep & Compile**. The Pi compiles `manifest.json`, archives the frames, and uploads the ZIP payload.
8.  The server processes the sweep and generates output results.

---

## 5. REST API Endpoints

### 5.1 Pi Edge Appliance Routes
*   `GET /` — Serves the control dashboard.
*   `GET /health` — Returns core temp, disk usage, and locking status.
*   `GET /stream` — MJPEG stream for alignment (auto-closes after 5 minutes of inactivity).
*   `GET /photo` — Legacy still capture route. Captures, warp-corrects, and returns a single JPEG.
*   `GET /calibrate` — Captures unwarped calibration photo.
*   `POST /sweep/start` — Initializes a capture session and starts background loop.
*   `POST /sweep/stop` — Stops the session and returns stats.
*   `GET /sweep/status` — Returns active frame counters.
*   `GET /sweep/sessions` — Lists stored sweep session folders.
*   `GET /sweep/<session_id>/zip` — Downloads a compiled ZIP archive.
*   `POST /sweep/<session_id>/upload` — Manually uploads the ZIP to the reconstruction server.
*   `DELETE /sweep/<session_id>` — Deletes session data.

### 5.2 Reconstruction Server Routes
*   `GET /health` — Server availability status.
*   `POST /process-sweep` — Accepts ZIP upload and queues background stitching tasks.
*   `GET /jobs/<job_id>` — Returns job status. If completed, returns full debug report.
*   `GET /jobs/<job_id>/result/reconstructed.jpg` — Final rectified composite image.
*   `GET /jobs/<job_id>/result/reconstructed.pdf` — Compiled document PDF.
*   `GET /jobs/<job_id>/result/ocr.json` — OCR word coordinates and text blocks.
*   `GET /jobs/<job_id>/result/debug_report.json` — Processing logs and audit stats.

---

## 6. Troubleshooting

### 6.1 Camera Not Found
*   Ensure the CSI cable is seated correctly and contacts face the right direction.
*   Run `rpicam-still --list-cameras` to check driver detection. If empty, verify `dtoverlay=imx708` is in `/boot/firmware/config.txt`.

### 6.2 Camera Resource Busy (409 Conflict)
*   Only one process can access `/dev/video0`. If a sweep is running, stream requests are rejected. Stop alignment streams before triggering a sweep.

### 6.3 Blurry Frames
*   The camera locks focus when a sweep starts. Make sure the lens is focused on the page before clicking start. If the page is plain white, PDAF may struggle; draw a dark border line or place temporary text to assist focus.

### 6.4 Stitching Fails
*   Stitching requires overlap. Sweep slower, ensuring a **60–80% frame overlap**.
*   Avoid rotational drifts (twisting the camera while sweeping). Keep sweeps parallel.

### 6.5 Thermal Throttling
*   If the Pi temperature exceeds $80^\circ\text{C}$, the daemon blocks new sweeps. Shut down the preview stream when not in use. Add a copper heatsink to the SoC chip.
