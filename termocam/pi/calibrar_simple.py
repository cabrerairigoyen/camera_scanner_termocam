import cv2
import json
import os

POINTS = []
IMAGE_PATH = 'autofocus_photo.jpg'
OUTPUT_JSON = '../warp_points.json'

def click_event(event, x, y, flags, params):
    global POINTS
    if event == cv2.EVENT_LBUTTONDOWN:
        if len(POINTS) < 4:
            POINTS.append([x, y])
            print(f"Point recorded: ({x}, {y})")
            
            # Draw a circle where the user clicked
            cv2.circle(img, (x, y), 5, (0, 0, 255), -1)
            
            # Connect the points with lines
            if len(POINTS) > 1:
                cv2.line(img, tuple(POINTS[-2]), tuple(POINTS[-1]), (0, 255, 0), 2)
            
            # Connect last point to first if 4 points are collected
            if len(POINTS) == 4:
                cv2.line(img, tuple(POINTS[-1]), tuple(POINTS[0]), (0, 255, 0), 2)
                
            cv2.imshow('Calibrar', img)

if not os.path.exists(IMAGE_PATH):
    # Try looking in parent dir since documentation says it's in camera_scanner_termocam
    if os.path.exists('../autofocus_photo.jpg'):
        IMAGE_PATH = '../autofocus_photo.jpg'
    else:
        print(f"Error: {IMAGE_PATH} not found. Please capture a photo first.")
        exit(1)

print("Loading image...")
img = cv2.imread(IMAGE_PATH)

# Resize for display if too large
h, w = img.shape[:2]
scale = 1.0
max_dim = 1000
if max(h, w) > max_dim:
    scale = max_dim / max(h, w)
    img = cv2.resize(img, (int(w * scale), int(h * scale)))
    
print("Please click the 4 corners of the document in the following order:")
print("1. Top-Left")
print("2. Top-Right")
print("3. Bottom-Right")
print("4. Bottom-Left")
print("Press 'c' to clear points, or any other key when done (after 4 points).")

cv2.imshow('Calibrar', img)
cv2.setMouseCallback('Calibrar', click_event)

while True:
    key = cv2.waitKey(0) & 0xFF
    if key == ord('c'):
        POINTS.clear()
        img = cv2.imread(IMAGE_PATH)
        if scale != 1.0:
            img = cv2.resize(img, (int(w * scale), int(h * scale)))
        cv2.imshow('Calibrar', img)
        print("Points cleared.")
    else:
        break

cv2.destroyAllWindows()

if len(POINTS) == 4:
    # Scale back to original resolution if we resized
    if scale != 1.0:
        POINTS = [[int(x / scale), int(y / scale)] for x, y in POINTS]
        
    with open(OUTPUT_JSON, 'w') as f:
        json.dump(POINTS, f, indent=2)
    print(f"Calibration successful! Points saved to {OUTPUT_JSON}")
else:
    print(f"Calibration incomplete. {len(POINTS)} points collected instead of 4.")
