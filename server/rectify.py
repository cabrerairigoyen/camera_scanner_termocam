import cv2
import numpy as np

# A4 Standard dimensions (300 DPI)
A4_WIDTH = 2480
A4_HEIGHT = 3508

from server.page_detector import page_detector

def rectify_to_a4(image: np.ndarray) -> tuple[np.ndarray, dict]:
    """
    Tries to detect the document boundaries using AI segmentation and rectify it to A4.
    If boundary detection fails, performs deskewing.
    Returns (rectified_image, metadata_dict).
    """
    if image is None:
        return None, {}
        
    print("Rectifier: Running strict page detection...")
    result = page_detector.detect_page(image, mode="strict")
    
    metadata = {
        "rectification_method": "natural_deskew",
        "a4_detected": result["page_detected"],
        "confidence": result["confidence"],
        "corners": result["corners"],
        "decision": result["decision"],
        "reason": result["reason"],
        "a4_geometry_score": result.get("a4_geometry_score", 0.0),
        "area_ratio": result.get("area_ratio", 0.0)
    }
    
    if result["decision"] == "safe_to_warp":
        print(f"Rectifier: Safe to warp (method: {result['method']}, conf: {result['confidence']:.2f}).")
        quad = np.array(result["corners"], dtype=np.float32)
        metadata["rectification_method"] = result["method"]
        return warp_to_a4(image, quad), metadata
        
    # Fallback: Deskew the image naturally
    print(f"Rectifier: Not safe to warp ({result['reason']}). Falling back to natural deskew.")
    return natural_deskew(image), metadata

def natural_deskew(image: np.ndarray) -> np.ndarray:
    """Deskews the image without aggressively stretching it."""
    return deskew_image(image)

def warp_to_a4(image: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """Warps an ordered 4-point polygon to a standard A4 canvas."""
    dst = np.array([
        [0, 0],
        [A4_WIDTH - 1, 0],
        [A4_WIDTH - 1, A4_HEIGHT - 1],
        [0, A4_HEIGHT - 1]
    ], dtype="float32")
    
    M = cv2.getPerspectiveTransform(pts, dst)
    return cv2.warpPerspective(image, M, (A4_WIDTH, A4_HEIGHT))


def detect_and_warp_quad(image: np.ndarray) -> np.ndarray:
    """
    Detects the largest quadrilateral contour and applies perspective warp to A4 size.
    Validates completeness (padding from edge and aspect ratio).
    Raises ValueError if the A4 document is incomplete (cut off).
    Returns warped image if successful, otherwise None.
    """
    h, w = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    
    # Blur and edge detection
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edged = cv2.Canny(blurred, 50, 150)
    
    # Find contours
    contours, _ = cv2.findContours(edged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
        
    # Sort contours by area descending
    contours = sorted(contours, key=cv2.contourArea, reverse=True)
    
    for c in contours:
        # Approximate the contour
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.02 * peri, True)
        
        # If our approximated contour has four points, we can assume we found a quad
        if len(approx) == 4 and cv2.contourArea(c) > (w * h * 0.15):
            # Order the points: Top-Left, Top-Right, Bottom-Right, Bottom-Left
            pts = approx.reshape(4, 2)
            ordered_pts = order_points(pts)
            
            # 1. Validate boundary completeness
            pad = 5 # 5 pixels margin from edge
            for pt in ordered_pts:
                px, py = pt[0], pt[1]
                if px <= pad or py <= pad or px >= (w - pad) or py >= (h - pad):
                    raise ValueError("Incomplete A4: Document touches the edge of the stitched canvas and is likely cut off.")
                    
            # 2. Validate aspect ratio (~1.414 for A4)
            width_top = np.linalg.norm(ordered_pts[0] - ordered_pts[1])
            width_bottom = np.linalg.norm(ordered_pts[3] - ordered_pts[2])
            height_left = np.linalg.norm(ordered_pts[0] - ordered_pts[3])
            height_right = np.linalg.norm(ordered_pts[1] - ordered_pts[2])
            
            avg_width = max(1.0, (width_top + width_bottom) / 2.0)
            avg_height = max(1.0, (height_left + height_right) / 2.0)
            
            aspect_ratio = max(avg_width, avg_height) / min(avg_width, avg_height)
            # A4 is 1.414. Allow between 1.2 and 1.6 to account for perspective distortion
            if not (1.2 < aspect_ratio < 1.6):
                print(f"Rectifier: Aspect ratio {aspect_ratio:.2f} is outside A4 bounds (1.2-1.6). Continuing search...")
                continue
            
            # Destination points for A4
            dst = np.array([
                [0, 0],
                [A4_WIDTH - 1, 0],
                [A4_WIDTH - 1, A4_HEIGHT - 1],
                [0, A4_HEIGHT - 1]
            ], dtype="float32")
            
            M = cv2.getPerspectiveTransform(ordered_pts, dst)
            warped = cv2.warpPerspective(image, M, (A4_WIDTH, A4_HEIGHT))
            return warped
            
    return None


def order_points(pts) -> np.ndarray:
    """
    Orders 4 coordinates as [Top-Left, Top-Right, Bottom-Right, Bottom-Left].
    """
    rect = np.zeros((4, 2), dtype="float32")
    
    # Top-Left point will have the smallest sum, Bottom-Right will have the largest sum
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    
    # Top-Right point will have the smallest difference, Bottom-Left will have the largest difference
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    
    return rect


def deskew_image(image: np.ndarray) -> np.ndarray:
    """
    Detects text line orientations using Hough Lines and rotates the image to deskew.
    """
    h, w = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    
    # Downsample for speed
    scale = 800.0 / h
    small = cv2.resize(gray, (int(w * scale), 800), interpolation=cv2.INTER_AREA)
    
    edges = cv2.Canny(small, 50, 150, apertureSize=3)
    lines = cv2.HoughLinesP(edges, 1, np.pi/180, 100, minLineLength=100, maxLineGap=10)
    
    if lines is None:
        return image
        
    angles = []
    for line in lines:
        x1, y1, x2, y2 = line[0]
        angle = np.arctan2(y2 - y1, x2 - x1) * 180 / np.pi
        
        # Filter horizontal-ish angles
        if -45 < angle < 45:
            angles.append(angle)
        elif angle > 45:
            angles.append(angle - 90)
        elif angle < -45:
            angles.append(angle + 90)
            
    if len(angles) < 5:
        return image
        
    median_angle = np.median(angles)
    
    # Rotate original image
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, median_angle, 1.0)
    rotated = cv2.warpAffine(image, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT, borderValue=(0,0,0))
    
    print(f"Rectifier: Rotated image by {median_angle:.2f} degrees to deskew.")
    return rotated


def crop_content_box(image: np.ndarray) -> np.ndarray:
    """
    Crops out dark border areas to leave only the document content.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 10, 255, cv2.THRESH_BINARY)
    
    # Find bounding box of all non-zero pixels
    coords = cv2.findNonZero(thresh)
    if coords is not None:
        x, y, w, h = cv2.boundingRect(coords)
        # Add safety padding
        pad = 20
        x_min = max(0, x - pad)
        y_min = max(0, y - pad)
        x_max = min(image.shape[1], x + w + pad)
        y_max = min(image.shape[0], y + h + pad)
        return image[y_min:y_max, x_min:x_max]
    return image
