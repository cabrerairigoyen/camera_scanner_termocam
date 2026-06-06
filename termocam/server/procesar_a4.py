import cv2
import json
import numpy as np
import os

IMAGE_PATH = '../autofocus_photo.jpg'
POINTS_JSON = '../warp_points.json'
OUTPUT_PATH = '../documento_a4.jpg'

# A4 parameters at 300 DPI
A4_WIDTH = 2480
A4_HEIGHT = 3508

def get_warp_matrix(pts, dst_width, dst_height):
    src_pts = np.array(pts, dtype=np.float32)
    dst_pts = np.array([
        [0, 0],
        [dst_width - 1, 0],
        [dst_width - 1, dst_height - 1],
        [0, dst_height - 1]
    ], dtype=np.float32)
    return cv2.getPerspectiveTransform(src_pts, dst_pts)

def main():
    if not os.path.exists(IMAGE_PATH):
        print(f"Error: {IMAGE_PATH} not found.")
        return
        
    if not os.path.exists(POINTS_JSON):
        print(f"Error: {POINTS_JSON} not found. Run calibrar_simple.py first.")
        return

    print("Loading image and calibration points...")
    img = cv2.imread(IMAGE_PATH)
    with open(POINTS_JSON, 'r') as f:
        points = json.load(f)

    if len(points) != 4:
        print("Error: warp_points.json must contain exactly 4 points.")
        return

    print("Applying perspective warp to A4 format (2480x3508)...")
    matrix = get_warp_matrix(points, A4_WIDTH, A4_HEIGHT)
    warped = cv2.warpPerspective(img, matrix, (A4_WIDTH, A4_HEIGHT))

    print("Enhancing contrast for OCR (CLAHE)...")
    # Convert to grayscale for CLAHE
    gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
    
    # Create CLAHE object
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8,8))
    enhanced = clahe.apply(gray)
    
    # Save the result
    cv2.imwrite(OUTPUT_PATH, enhanced)
    print(f"✅ Processing complete! Document saved to {OUTPUT_PATH}")

if __name__ == "__main__":
    main()
