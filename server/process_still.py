import os
import cv2
import numpy as np
import json
import shutil
from PIL import Image

from server.rectify import rectify_to_a4
from server.enhance import enhance_for_ocr
from server.ocr import run_ocr
from server.debug_report import generate_debug_report

def process_highres_still(image_path: str, job_id: str, jobs_dir: str) -> dict:
    job_path = os.path.join(jobs_dir, job_id)
    os.makedirs(job_path, exist_ok=True)
    
    reconstructed_jpg_path = os.path.join(job_path, "reconstructed.jpg")
    reconstructed_pdf_path = os.path.join(job_path, "reconstructed.pdf")
    ocr_json_path = os.path.join(job_path, "ocr.json")
    debug_report_path = os.path.join(job_path, "debug_report.json")
    
    output_files = {}
    
    image = cv2.imread(image_path)
    if image is None:
        raise ValueError("Could not read uploaded high-res image.")
        
    print(f"Server Job {job_id}: Analyzing and rectifying high-res still...")
    
    # Run the rigorous AI/OpenCV pipeline
    rectified, meta = rectify_to_a4(image)
    
    # 1. Save Debug Artifacts
    overlay_img = image.copy()
    if meta.get("corners"):
        pts = np.array(meta["corners"], np.int32).reshape((-1, 1, 2))
        color = (0, 255, 0) if meta.get("decision") == "safe_to_warp" else (0, 0, 255)
        cv2.polylines(overlay_img, [pts], True, color, 8)
        
    cv2.imwrite(os.path.join(job_path, "page_detection_overlay.jpg"), overlay_img)
    with open(os.path.join(job_path, "page_quad.json"), "w") as f:
        json.dump(meta, f, indent=2)
    with open(os.path.join(job_path, "rectification_decision.json"), "w") as f:
        json.dump({"decision": meta.get("decision"), "reason": meta.get("reason")}, f, indent=2)
        
    # 2. Hard-Negative Collection
    conf = meta.get("confidence", 0.0)
    decision = meta.get("decision", "no_page_detected")
    if (0.30 <= conf <= 0.80) or decision == "not_safe_to_warp":
        review_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "page_detector_review")
        os.makedirs(review_dir, exist_ok=True)
        base_name = f"{job_id}_{conf:.2f}"
        
        cv2.imwrite(os.path.join(review_dir, f"{base_name}_orig.jpg"), image)
        cv2.imwrite(os.path.join(review_dir, f"{base_name}_overlay.jpg"), overlay_img)
        with open(os.path.join(review_dir, f"{base_name}_meta.json"), "w") as f:
            json.dump(meta, f, indent=2)
        print(f"Server Job {job_id}: Saved to Hard-Negative collection ({conf:.2f}, {decision}).")
        
    print(f"Server Job {job_id}: Enhancing for OCR...")
    enhanced = enhance_for_ocr(rectified)
    
    cv2.imwrite(reconstructed_jpg_path, enhanced, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
    output_files["reconstructed_image"] = os.path.basename(reconstructed_jpg_path)
    
    print(f"Server Job {job_id}: Generating PDF...")
    with Image.open(reconstructed_jpg_path) as im:
        im.save(reconstructed_pdf_path, "PDF")
    output_files["pdf"] = os.path.basename(reconstructed_pdf_path)
    
    print(f"Server Job {job_id}: Performing OCR...")
    ocr_result = run_ocr(reconstructed_jpg_path)
    with open(ocr_json_path, "w") as f:
        json.dump(ocr_result, f, indent=2)
    output_files["ocr_json"] = os.path.basename(ocr_json_path)
        
    debug_info = {
        "job_id": job_id,
        "type": "highres_still",
        "page_detection": meta,
        "output_files": output_files
    }
    
    # We will update debug_report generation to include the page_detection key
    generate_debug_report(
        job_id=job_id,
        session_id="",
        input_stats={"frame_count": 1, "resolution": list(image.shape[:2])},
        stitching_stats={"method_used": "none (still)", "status": "skipped"},
        quality_stats={"mean_sharpness": 0, "rejected_server_side": 0},
        ocr_stats={
            "engine": ocr_result.get("engine", "unknown") if ocr_result else "failed",
            "line_count": len(ocr_result.get("lines", [])) if ocr_result else 0,
            "mean_confidence": (
                sum(line.get("confidence", 0.0) for line in ocr_result.get("lines", []))
                / max(len(ocr_result.get("lines", [])), 1)
            ) if ocr_result else 0.0,
        },
        output_files=output_files,
        report_output_path=debug_report_path,
    )
    
    # Read the generated report and inject page_detection
    try:
        with open(debug_report_path, "r") as f:
            report_data = json.load(f)
        report_data["page_detection"] = meta
        with open(debug_report_path, "w") as f:
            json.dump(report_data, f, indent=2)
    except Exception as e:
        print(f"Server Job {job_id}: Could not inject page_detection into report: {e}")
    
    print(f"Server Job {job_id} Completed successfully.")
    return debug_info
