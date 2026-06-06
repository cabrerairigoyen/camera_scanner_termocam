import json
import os

def generate_debug_report(
    job_id: str,
    session_id: str,
    input_stats: dict,
    stitching_stats: dict,
    quality_stats: dict,
    ocr_stats: dict,
    output_files: dict,
    report_output_path: str
) -> dict:
    """
    Assembles process run statistics and configurations, and writes out a
    standardized debug_report.json file.
    
    Args:
        job_id (str): The unique identifier for the server job.
        session_id (str): The Pi session identifier.
        input_stats (dict): Contains 'frame_count' and 'resolution'.
        stitching_stats (dict): Contains 'method_attempted', 'method_used', 'status', 'opencv_status_code', and 'warnings'.
        quality_stats (dict): Contains 'mean_sharpness', 'min_sharpness', and 'rejected_server_side'.
        ocr_stats (dict): Contains 'engine', 'line_count', and 'mean_confidence'.
        output_files (dict): Relative/absolute paths of outputs: reconstructed_image, pdf, ocr_json.
        report_output_path (str): File path where to write the JSON report.
        
    Returns:
        dict: The created debug report dictionary.
    """
    report = {
        "job_id": job_id,
        "session_id": session_id,
        "input": {
            "frame_count": input_stats.get("frame_count", 0),
            "resolution": input_stats.get("resolution", [0, 0])
        },
        "stitching": {
            "method_attempted": stitching_stats.get("method_attempted", []),
            "method_used": stitching_stats.get("method_used", "none"),
            "status": stitching_stats.get("status", "failed"),
            "opencv_status_code": stitching_stats.get("opencv_status_code", -1),
            "warnings": stitching_stats.get("warnings", [])
        },
        "quality": {
            "mean_sharpness": round(quality_stats.get("mean_sharpness", 0.0), 2),
            "min_sharpness": round(quality_stats.get("min_sharpness", 0.0), 2),
            "rejected_server_side": quality_stats.get("rejected_server_side", 0)
        },
        "ocr": {
            "engine": ocr_stats.get("engine", "none"),
            "line_count": ocr_stats.get("line_count", 0),
            "mean_confidence": round(ocr_stats.get("mean_confidence", 0.0), 2)
        },
        "outputs": {
            "reconstructed_image": output_files.get("reconstructed_image", ""),
            "pdf": output_files.get("pdf", ""),
            "ocr_json": output_files.get("ocr_json", "")
        }
    }
    
    # Save to file
    try:
        os.makedirs(os.path.dirname(report_output_path), exist_ok=True)
        with open(report_output_path, 'w') as f:
            json.dump(report, f, indent=2)
    except Exception as e:
        print(f"DebugReport Error: Failed to save report to {report_output_path}: {e}")
        
    return report
