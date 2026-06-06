import os
import zipfile
import tempfile
import shutil
import json
import numpy as np
import cv2
from PIL import Image

from server.stitch import stitch_images
from server.rectify import rectify_to_a4
from server.enhance import enhance_for_ocr
from server.ocr import run_ocr
from server.debug_report import generate_debug_report
from pi.capture.frame_quality import sharpness_laplacian


def process_sweep_zip(zip_path: str, job_id: str, jobs_dir: str) -> dict:
    """
    Main entry point for processing a sweep session ZIP archive on the server.
    
    Args:
        zip_path (str): File path of the uploaded session ZIP.
        job_id (str): The unique server job ID.
        jobs_dir (str): Directory containing job output artifacts.
        
    Returns:
        dict: The final debug report.
    """
    job_path = os.path.join(jobs_dir, job_id)
    os.makedirs(job_path, exist_ok=True)
    
    # Define job outputs
    reconstructed_jpg_path = os.path.join(job_path, "reconstructed.jpg")
    reconstructed_pdf_path = os.path.join(job_path, "reconstructed.pdf")
    ocr_json_path = os.path.join(job_path, "ocr.json")
    debug_report_path = os.path.join(job_path, "debug_report.json")
    
    # Intermediate stats tracking
    session_id = "unknown"
    input_stats = {"frame_count": 0, "resolution": [0, 0]}
    stitching_stats = {
        "method_attempted": ["SCANS", "PANORAMA", "CUSTOM_FEATURE"],
        "method_used": "none",
        "status": "failed",
        "opencv_status_code": -1,
        "warnings": []
    }
    quality_stats = {"mean_sharpness": 0.0, "min_sharpness": 0.0, "rejected_server_side": 0}
    ocr_stats = {"engine": "none", "line_count": 0, "mean_confidence": 0.0}
    output_files = {}
    
    # Create temporary directory for extraction
    temp_dir = tempfile.mkdtemp(prefix=f"sweep_process_{job_id}_")
    
    try:
        # 1. Unzip the session
        print(f"Server Job {job_id}: Extracting ZIP...")
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(temp_dir)
            
        # 2. Read manifest.json
        manifest_path = os.path.join(temp_dir, "manifest.json")
        if not os.path.exists(manifest_path):
            raise FileNotFoundError("ZIP file lacks manifest.json")
            
        with open(manifest_path, 'r') as f:
            manifest = json.load(f)
            
        session_id = manifest.get("session_id", "unknown")
        input_stats["resolution"] = manifest.get("camera", {}).get("resolution", [0, 0])
        
        # 3. Load accepted frames
        accepted_entries = manifest.get("frames", [])
        input_stats["frame_count"] = len(accepted_entries)
        
        if len(accepted_entries) == 0:
            stitching_stats["warnings"].append("No accepted frames in session manifest.")
            raise ValueError("Zero accepted frames to process.")
            
        # Load and compute sharpness metrics on server side
        frames = []
        sharpnesses = []
        
        for entry in accepted_entries:
            frame_rel_path = entry.get("filename") # e.g. "frames/frame_000001.jpg"
            frame_path = os.path.join(temp_dir, frame_rel_path)
            
            if os.path.exists(frame_path):
                img = cv2.imread(frame_path)
                if img is not None:
                    frames.append(img)
                    
                    # Compute sharpness
                    sh = sharpness_laplacian(img)
                    sharpnesses.append(sh)
                else:
                    stitching_stats["warnings"].append(f"Failed to read image file: {frame_rel_path}")
            else:
                stitching_stats["warnings"].append(f"Image file missing from ZIP: {frame_rel_path}")
                
        # Validate loaded frames count
        if len(frames) == 0:
            raise ValueError("No valid frame images could be loaded from ZIP file.")
            
        # Calculate quality stats
        quality_stats["mean_sharpness"] = np.mean(sharpnesses) if sharpnesses else 0.0
        quality_stats["min_sharpness"] = np.min(sharpnesses) if sharpnesses else 0.0
        
        # 4. Stitch frames
        status, stitched, status_code, method_used = stitch_images(frames)
        stitching_stats["status"] = status
        stitching_stats["opencv_status_code"] = status_code
        stitching_stats["method_used"] = method_used
        
        if status != "success" or stitched is None:
            raise RuntimeError(f"Stitching failed (Method: {method_used}, Code: {status_code}).")
            
        # 5. Rectify stitched output
        print(f"Server Job {job_id}: Rectifying stitched image...")
        rectified = rectify_to_a4(stitched)
        
        # 6. Enhance rectified output
        print(f"Server Job {job_id}: Enhancing for OCR...")
        enhanced = enhance_for_ocr(rectified)
        
        # 7. Save reconstructed image
        cv2.imwrite(reconstructed_jpg_path, enhanced, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
        output_files["reconstructed_image"] = os.path.basename(reconstructed_jpg_path)
        
        # 8. Save PDF
        print(f"Server Job {job_id}: Generating PDF...")
        # Open saved image with Pillow to write out PDF
        with Image.open(reconstructed_jpg_path) as im:
            # Pillow converts BGR/RGB properly. Standard format is PDF.
            im.save(reconstructed_pdf_path, "PDF")
        output_files["pdf"] = os.path.basename(reconstructed_pdf_path)
        
        # 9. OCR Processing
        print(f"Server Job {job_id}: Performing OCR...")
        ocr_result = run_ocr(reconstructed_jpg_path)
        
        # Save OCR JSON
        with open(ocr_json_path, 'w') as f:
            json.dump(ocr_result, f, indent=2)
        output_files["ocr_json"] = os.path.basename(ocr_json_path)
        
        # Update OCR stats
        ocr_stats["engine"] = ocr_result.get("engine", "none")
        ocr_stats["line_count"] = len(ocr_result.get("lines", []))
        
        confidences = [line["confidence"] for line in ocr_result.get("lines", [])]
        ocr_stats["mean_confidence"] = np.mean(confidences) if confidences else 0.0
        
    except Exception as e:
        print(f"Server Job {job_id} Pipeline Error: {e}")
        stitching_stats["status"] = "failed"
        stitching_stats["warnings"].append(str(e))
        
    finally:
        # Clean up temporary folders
        try:
            shutil.rmtree(temp_dir)
        except Exception as e:
            print(f"Failed to clean up temp dir {temp_dir}: {e}")
            
    # Generate final debug report
    report = generate_debug_report(
        job_id=job_id,
        session_id=session_id,
        input_stats=input_stats,
        stitching_stats=stitching_stats,
        quality_stats=quality_stats,
        ocr_stats=ocr_stats,
        output_files=output_files,
        report_output_path=debug_report_path
    )
    
    return report
