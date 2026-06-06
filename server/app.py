import os
import uuid
import time
import shutil
from fastapi import FastAPI, UploadFile, File, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from server.process_sweep import process_sweep_zip

app = FastAPI(title="TermoCam Reconstruction Server", version="1.0.0")

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

# ----------------- Background Processing Worker -----------------

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
        with open(zip_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
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
