import os
import uuid
import time
import shutil
from fastapi import FastAPI, UploadFile, File, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from dotenv import load_dotenv

# Load workspace .env for Mathpix credentials
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env"))

from server.process_sweep import process_sweep_zip

app = FastAPI(title="TermoCam Reconstruction Server", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Base Directories
script_dir = os.path.dirname(os.path.abspath(__file__))
data_dir = os.path.join(script_dir, "data")
jobs_dir = os.path.join(data_dir, "jobs")
uploads_dir = os.path.join(data_dir, "uploads")

os.makedirs(jobs_dir, exist_ok=True)
os.makedirs(uploads_dir, exist_ok=True)

# In-memory status tracking for active runs
# Values: "pending", "running", "completed", "failed"
job_statuses = {}

import cv2
import numpy as np

# Page detection import removed (it is now imported locally in routes where needed)
from server.capture_quality import evaluate_capture_quality
from server.camera_feedback import generate_feedback_instruction
from server.process_still import process_highres_still

# ----------------- Background Processing Worker -----------------

def run_still_pipeline_worker(image_path: str, job_id: str):
    """Worker task executed in FastAPI background thread for still capture."""
    global job_statuses
    job_statuses[job_id] = "running"
    
    try:
        process_highres_still(image_path, job_id, jobs_dir)
        
        # Verify if report was written
        report_path = os.path.join(jobs_dir, job_id, "debug_report.json")
        if os.path.exists(report_path):
            job_statuses[job_id] = "completed"
        else:
            job_statuses[job_id] = "failed"
            
    except Exception as e:
        print(f"Worker Error for Still Job {job_id}: {e}")
        job_statuses[job_id] = "failed"
        
    finally:
        # Safely clean up uploaded image file
        if os.path.exists(image_path):
            try:
                os.remove(image_path)
            except Exception as e:
                print(f"Failed to delete uploaded image {image_path}: {e}")

def run_pipeline_worker(zip_path: str, job_id: str):
    """Worker task executed in FastAPI background thread."""
    global job_statuses
    job_statuses[job_id] = "running"
    
    try:
        process_sweep_zip(zip_path, job_id, jobs_dir)
        
        # Verify if report was written
        report_path = os.path.join(jobs_dir, job_id, "debug_report.json")
        if os.path.exists(report_path):
            job_statuses[job_id] = "completed"
        else:
            job_statuses[job_id] = "failed"
            
    except Exception as e:
        print(f"Worker Error for Job {job_id}: {e}")
        job_statuses[job_id] = "failed"
        
    finally:
        # Safely clean up uploaded ZIP file
        if os.path.exists(zip_path):
            try:
                os.remove(zip_path)
            except Exception as e:
                print(f"Failed to delete uploaded zip {zip_path}: {e}")

# ----------------- REST Endpoints -----------------

@app.get("/health")
def health():
    """Server health check."""
    return {"status": "ok", "uptime_sec": time.monotonic()}


@app.post("/detect-page-preview")
async def detect_page_preview(file: UploadFile = File(...)):
    """Receives a low-res preview frame from the UI and returns page detection geometry."""
    try:
        content = await file.read()
        np_arr = np.frombuffer(content, np.uint8)
        image = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        
        if image is None:
            raise ValueError("Invalid image")
            
        h, w = image.shape[:2]
        
        from server.page_detector import page_detector
        from server.camera_feedback import generate_feedback_instruction
        
        # Detect page
        result = page_detector.detect_page(image, mode="preview")
        print(f"Preview Result: {result}")
        
        # Determine instruction
        instruction = generate_feedback_instruction(result, w, h)
        
        from server.page_detector import AUTO_CAPTURE_THRESHOLD
        capture_ready = result["confidence"] >= AUTO_CAPTURE_THRESHOLD and result["decision"] != "no_page_detected"
        
        return {
            "page_detected": result["page_detected"],
            "confidence": result["confidence"],
            "corners": result["corners"],
            "instruction": instruction,
            "capture_ready": capture_ready,
            "method": result["method"]
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/process-still")
async def process_still(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    """Accepts a single high-resolution image for AI-assisted rectification and OCR."""
    if not file.filename.lower().endswith(('.jpg', '.jpeg', '.png')):
        raise HTTPException(status_code=400, detail="Invalid file type. Only images are supported.")
        
    job_id = f"job_still_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    job_statuses[job_id] = "pending"
    
    img_path = os.path.join(uploads_dir, f"{job_id}{os.path.splitext(file.filename)[1]}")
    try:
        content = await file.read()
        with open(img_path, "wb") as buffer:
            buffer.write(content)
    except Exception as e:
        job_statuses[job_id] = "failed"
        raise HTTPException(status_code=500, detail=f"Failed to store upload on server: {e}")
        
    background_tasks.add_task(run_still_pipeline_worker, img_path, job_id)
    
    return {
        "job_id": job_id,
        "status": "pending",
        "message": "Upload successful. Still image processing queued."
    }


@app.post("/process-sweep")
async def process_sweep(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    """
    Accepts multipart upload of a sweep session ZIP archive,
    saves it, and schedules processing in a background worker task.
    """
    if not file.filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail="Invalid file type. Only ZIP archives are supported.")
        
    # Generate unique Job ID
    job_id = f"job_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    job_statuses[job_id] = "pending"
    
    # Save the upload to file
    zip_path = os.path.join(uploads_dir, f"{job_id}.zip")
    try:
        content = await file.read()
        with open(zip_path, "wb") as buffer:
            buffer.write(content)
    except Exception as e:
        job_statuses[job_id] = "failed"
        raise HTTPException(status_code=500, detail=f"Failed to store upload on server: {e}")
        
    # Queue the background pipeline run
    background_tasks.add_task(run_pipeline_worker, zip_path, job_id)
    
    return {
        "job_id": job_id,
        "status": "pending",
        "message": "Upload successful. Session added to queue."
    }


@app.get("/jobs/{job_id}")
def get_job_status(job_id: str):
    """Returns the processing status of a job, loading the full report if completed."""
    # Check in-memory status first
    status = job_statuses.get(job_id)
    
    # If not in memory, query file system (could have completed in a previous daemon run)
    job_path = os.path.join(jobs_dir, job_id)
    report_path = os.path.join(job_path, "debug_report.json")
    
    if os.path.exists(report_path):
        try:
            with open(report_path, 'r') as f:
                report = json.load(f)
            return report
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to parse debug report: {e}")
            
    if status is None:
        raise HTTPException(status_code=404, detail="Job ID not found.")
        
    return {
        "job_id": job_id,
        "status": status,
        "message": f"Job is currently {status}."
    }


@app.get("/jobs/{job_id}/result/reconstructed.jpg")
def get_reconstructed_jpg(job_id: str):
    """Serves the final stitched and rectified A4 JPEG image."""
    file_path = os.path.join(jobs_dir, job_id, "reconstructed.jpg")
    if os.path.exists(file_path):
        return FileResponse(file_path, media_type="image/jpeg")
    raise HTTPException(status_code=404, detail="Reconstructed JPEG not found or processing incomplete.")


@app.get("/jobs/{job_id}/result/reconstructed.pdf")
def get_reconstructed_pdf(job_id: str):
    """Serves the final PDF document."""
    file_path = os.path.join(jobs_dir, job_id, "reconstructed.pdf")
    if os.path.exists(file_path):
        return FileResponse(file_path, media_type="application/pdf", filename=f"reconstructed_{job_id}.pdf")
    raise HTTPException(status_code=404, detail="Reconstructed PDF not found or processing incomplete.")


@app.get("/jobs/{job_id}/result/ocr.json")
def get_ocr_json(job_id: str):
    """Serves the structured OCR text results."""
    file_path = os.path.join(jobs_dir, job_id, "ocr.json")
    if os.path.exists(file_path):
        return FileResponse(file_path, media_type="application/json")
    raise HTTPException(status_code=404, detail="OCR JSON not found or processing incomplete.")


@app.get("/jobs/{job_id}/result/debug_report.json")
def get_debug_report_json(job_id: str):
    """Serves the debug report metadata directly as a JSON file."""
    file_path = os.path.join(jobs_dir, job_id, "debug_report.json")
    if os.path.exists(file_path):
        return FileResponse(file_path, media_type="application/json")
    raise HTTPException(status_code=404, detail="Debug report not found or processing incomplete.")
