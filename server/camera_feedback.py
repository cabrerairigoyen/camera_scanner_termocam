import numpy as np

def generate_feedback_instruction(result: dict, img_width: int, img_height: int) -> str:
    """Translates geometry and confidence scores into strict directional enums."""
    if not result["page_detected"] or not result["corners"]:
        return "no_page_detected"
        
    pts = np.array(result["corners"], dtype=np.float32)
    
    # 1. Calculate bounding box and center
    min_x = np.min(pts[:, 0])
    max_x = np.max(pts[:, 0])
    min_y = np.min(pts[:, 1])
    max_y = np.max(pts[:, 1])
    
    quad_center_x = (min_x + max_x) / 2
    quad_center_y = (min_y + max_y) / 2
    
    img_center_x = img_width / 2
    img_center_y = img_height / 2
    
    # 2. Check Clipping
    pad_x = img_width * 0.05
    pad_y = img_height * 0.05
    if min_x < pad_x or max_x > img_width - pad_x or min_y < pad_y or max_y > img_height - pad_y:
        return "move_farther"
        
    # 3. Check Scale
    if result.get("area_ratio", 0) < 0.25:
        return "move_closer"
        
    # 4. Check Centering (if it's off by more than 15% of the frame)
    offset_x = (quad_center_x - img_center_x) / img_width
    offset_y = (quad_center_y - img_center_y) / img_height
    
    if offset_x < -0.15:
        return "move_left"
    if offset_x > 0.15:
        return "move_right"
    if offset_y < -0.15:
        return "move_up"
    if offset_y > 0.15:
        return "move_down"
        
    # 5. Check Tilt/Skew
    w_top = np.linalg.norm(pts[0] - pts[1])
    w_bottom = np.linalg.norm(pts[3] - pts[2])
    
    if w_top > 0 and w_bottom / w_top > 1.25:
        return "reduce_tilt"
    if w_bottom > 0 and w_top / w_bottom > 1.25:
        return "reduce_tilt"
        
    # Check rotation (are the top corners leveled?)
    angle_rad = np.arctan2(pts[1][1] - pts[0][1], pts[1][0] - pts[0][0])
    angle_deg = np.degrees(angle_rad)
    if angle_deg > 5:
        return "rotate_counterclockwise"
    if angle_deg < -5:
        return "rotate_clockwise"
        
    from server.page_detector import AUTO_CAPTURE_THRESHOLD
    if result["confidence"] < AUTO_CAPTURE_THRESHOLD:
        return "hold_still"
        
    return "ready"
