import cv2
import numpy as np

def calculate_sharpness_score(image: np.ndarray) -> float:
    """Calculates a normalized sharpness score using Laplacian variance."""
    if image is None: return 0.0
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
    variance = cv2.Laplacian(gray, cv2.CV_64F).var()
    # Normalize: >500 is very sharp, <100 is blurry.
    score = min(max((variance - 50.0) / 450.0, 0.0), 1.0)
    return float(score)

def calculate_lighting_score(image: np.ndarray) -> float:
    """Calculates lighting quality (penalizes under/overexposure)."""
    if image is None: return 0.0
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    brightness = hsv[..., 2].mean()
    # Ideal brightness is around 120-200.
    if brightness < 50: return 0.2
    if brightness > 240: return 0.2
    if 100 <= brightness <= 200: return 1.0
    if brightness < 100:
        return float((brightness - 50) / 50.0 * 0.8 + 0.2)
    return float((240 - brightness) / 40.0 * 0.8 + 0.2)

def calculate_geometry_score(corners: list, img_width: int, img_height: int) -> float:
    """Evaluates how perfectly the quadrilateral fits the A4 aspect ratio and fills the frame."""
    if not corners or len(corners) != 4:
        return 0.0
        
    pts = np.array(corners, dtype=np.float32)
    
    # Calculate widths
    w_top = np.linalg.norm(pts[0] - pts[1])
    w_bottom = np.linalg.norm(pts[3] - pts[2])
    # Calculate heights
    h_left = np.linalg.norm(pts[0] - pts[3])
    h_right = np.linalg.norm(pts[1] - pts[2])
    
    max_w = max(w_top, w_bottom)
    max_h = max(h_left, h_right)
    
    if max_w == 0 or max_h == 0: return 0.0
    
    # A4 Aspect ratio is sqrt(2) approx 1.414
    aspect = max_h / max_w
    aspect_diff = abs(aspect - 1.414)
    # Score drops if aspect is far from 1.414
    aspect_score = max(1.0 - (aspect_diff * 2.0), 0.0)
    
    # Calculate how much area it takes up
    area = max_w * max_h
    frame_area = img_width * img_height
    fill_ratio = area / frame_area
    # Ideal fill ratio is between 0.4 and 0.8. If it's < 0.2 it's too small.
    if fill_ratio < 0.2:
        fill_score = fill_ratio / 0.2 * 0.5
    elif fill_ratio > 0.9:
        fill_score = 0.5 # Too close to the edge, might be cropped
    else:
        fill_score = 1.0
        
    return float((aspect_score * 0.6) + (fill_score * 0.4))

def evaluate_capture_quality(image: np.ndarray, page_confidence: float, corners: list, stability_score: float = 1.0) -> dict:
    """Aggregates all scores to determine if the page is ready for capture."""
    h, w = image.shape[:2]
    
    sharpness = calculate_sharpness_score(image)
    lighting = calculate_lighting_score(image)
    geometry = calculate_geometry_score(corners, w, h)
    
    # Weightings based on architecture proposal
    score = (
        0.35 * page_confidence +
        0.20 * geometry +
        0.20 * sharpness +
        0.15 * lighting +
        0.10 * stability_score
    )
    
    return {
        "score": score,
        "sharpness": sharpness,
        "lighting": lighting,
        "geometry": geometry,
        "capture_ready": score > 0.75 # 0.85 might be too strict for a fallback OpenCV detector
    }
