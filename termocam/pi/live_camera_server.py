import cv2
import time
import threading
import json
import subprocess
import os
import numpy as np
from flask import Flask, Response, request, send_file, jsonify

app = Flask(__name__)

# Config
STREAM_WIDTH = 640
STREAM_HEIGHT = 480
STREAM_FPS = 10
HIGH_RES_WIDTH = 4608
HIGH_RES_HEIGHT = 2592

# State
camera_lock = threading.Lock()
transform_config = {
    'rotation': 0, # degrees
    'warp_points': None # [[x,y], [x,y], [x,y], [x,y]]
}
latest_photo_path = "latest_photo.jpg"

def get_warp_matrix(pts, width, height):
    """Calculates perspective transform matrix given 4 corners."""
    src_pts = np.array(pts, dtype=np.float32)
    # Output standard rectangle
    dst_pts = np.array([
        [0, 0],
        [width - 1, 0],
        [width - 1, height - 1],
        [0, height - 1]
    ], dtype=np.float32)
    return cv2.getPerspectiveTransform(src_pts, dst_pts)

def apply_transform(frame):
    """Applies rotation and perspective warp based on config."""
    if frame is None: return None
    
    # 1. Rotation
    rot = transform_config.get('rotation', 0)
    if rot == 90:
        frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    elif rot == 180:
        frame = cv2.rotate(frame, cv2.ROTATE_180)
    elif rot == 270:
        frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
        
    # 2. Perspective Warp
    warp_pts = transform_config.get('warp_points')
    if warp_pts and len(warp_pts) == 4:
        h, w = frame.shape[:2]
        matrix = get_warp_matrix(warp_pts, w, h)
        frame = cv2.warpPerspective(frame, matrix, (w, h))
        
    return frame

def generate_frames():
    """Generator for MJPEG stream using v4l2 camera."""
    while True:
        with camera_lock:
            # We open and close the camera rapidly so it can be freed for high-res capture.
            # In a production environment, you might keep it open and only release on demand.
            cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                time.sleep(1.0)
                continue
                
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, STREAM_WIDTH)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, STREAM_HEIGHT)
            cap.set(cv2.CAP_PROP_FPS, STREAM_FPS)
            
            success, frame = cap.read()
            cap.release()
            
            if success:
                frame = apply_transform(frame)
                ret, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
                frame_bytes = buffer.tobytes()
                
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
            else:
                time.sleep(0.1)
                
@app.route('/video_feed')
def video_feed():
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/capture_photo', methods=['POST'])
def capture_photo():
    """Takes a high-res photo using libcamera-still, pausing the stream."""
    with camera_lock:
        print("Capturing high-res photo...")
        # Use libcamera-still for max quality and autofocus
        cmd = [
            "libcamera-still",
            "-o", latest_photo_path,
            "--width", str(HIGH_RES_WIDTH),
            "--height", str(HIGH_RES_HEIGHT),
            "--autofocus-mode", "auto",
            "-t", "2000", # 2 seconds to focus
            "--nopreview"
        ]
        try:
            subprocess.run(cmd, check=True)
            return jsonify({"status": "success", "file": latest_photo_path})
        except subprocess.CalledProcessError as e:
            print("Capture failed:", e)
            return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/latest_photo')
def get_latest_photo():
    if os.path.exists(latest_photo_path):
        return send_file(latest_photo_path, mimetype='image/jpeg')
    return "No photo available", 404

@app.route('/set_transform', methods=['POST'])
def set_transform():
    global transform_config
    data = request.json
    if data:
        if 'rotation' in data:
            transform_config['rotation'] = data['rotation']
        if 'warp_points' in data:
            transform_config['warp_points'] = data['warp_points']
    return jsonify({"status": "success", "config": transform_config})

if __name__ == '__main__':
    # Allow CORS by using 0.0.0.0
    app.run(host='0.0.0.0', port=5000, threaded=True)
