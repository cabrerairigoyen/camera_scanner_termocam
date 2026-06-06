import cv2
import numpy as np

# A4 Standard dimensions (300 DPI)
A4_WIDTH = 2480
A4_HEIGHT = 3508

def rectify_to_a4(image: np.ndarray) -> np.ndarray:
    """
    Tries to detect the document boundaries and rectify it to A4.
    If boundary detection fails, performs deskewing and stretches 
    the bounding box to standard A4 dimensions.
    """
    if image is None:
        return None
        
    # 1. First find coordinates if a clear quadrilateral contour exists
    rectified = detect_and_warp_quad(image)
    if rectified is not None:
        print("Rectifier: Successfully warped document using detected quadrilateral.")
        return rectified
        
    # 2. Fallback: Deskew the image, crop black regions, and resize to A4
    print("Rectifier: Quadrilateral detection inconclusive. Falling back to deskew and fit...")
    deskewed = deskew_image(image)
    cropped = crop_content_box(deskewed)
    
    # Resize cropped image to standard A4 size
    rectified_fallback = cv2.resize(cropped, (A4_WIDTH, A4_HEIGHT), interpolation=cv2.INTER_CUBIC)
    return rectified_fallback


def detect_and_warp_quad(image: np.ndarray) -> np.ndarray:
    """
    Detects the largest quadrilateral contour and applies perspective warp to A4 size.
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
