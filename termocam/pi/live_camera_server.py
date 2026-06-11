import cv2
import time
import threading
import json
import subprocess
import os
import numpy as np
from collections import deque
from flask import Flask, Response, request, send_file, jsonify, render_template_string

app = Flask(__name__)

# Config
# Keep the live preview wide enough for document framing while staying responsive.
STREAM_WIDTH = 2304
STREAM_HEIGHT = 1296
STREAM_FPS = 10
HIGH_RES_WIDTH = 4608
HIGH_RES_HEIGHT = 2592
PREVIEW_WIDTH = 1280
PREVIEW_HEIGHT = 720

# State
camera_lock = threading.Lock()
stream_process = None
stream_process_lock = threading.Lock()
stop_stream_requested = False
transform_config = {
    'rotation': 0, # degrees
    'warp_points': None # [[x,y], [x,y], [x,y], [x,y]]
}
latest_photo_path = "latest_photo.jpg"

CONTROL_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>TermoCam Live Control</title>
    <style>
        :root {
            --bg: #08101a;
            --panel: rgba(16, 24, 40, 0.92);
            --line: rgba(255,255,255,0.08);
            --text: #eef2ff;
            --muted: #94a3b8;
            --accent: #60a5fa;
            --accent2: #34d399;
        }
        * { box-sizing: border-box; }
        body {
            margin: 0;
            min-height: 100vh;
            font-family: Arial, Helvetica, sans-serif;
            color: var(--text);
            background:
                radial-gradient(circle at top left, rgba(96,165,250,0.18), transparent 35%),
                radial-gradient(circle at bottom right, rgba(52,211,153,0.10), transparent 30%),
                var(--bg);
        }
        .wrap {
            width: min(1400px, calc(100vw - 24px));
            margin: 0 auto;
            padding: 12px;
        }
        .top {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 16px;
            margin-bottom: 12px;
        }
        h1 {
            font-size: 24px;
            margin: 0;
        }
        .sub {
            color: var(--muted);
            font-size: 13px;
            margin-top: 4px;
        }
        .badge {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 8px 12px;
            border-radius: 999px;
            background: rgba(255,255,255,0.06);
            border: 1px solid var(--line);
            font-size: 13px;
        }
        .dot {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            background: #ef4444;
            box-shadow: 0 0 0 0 rgba(239,68,68,0.45);
            animation: pulse 1.2s infinite;
        }
        @keyframes pulse {
            0% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(239,68,68,0.45); }
            70% { transform: scale(1); box-shadow: 0 0 0 12px rgba(239,68,68,0); }
            100% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(239,68,68,0); }
        }
        .grid {
            display: grid;
            grid-template-columns: minmax(0, 1.7fr) minmax(320px, 0.8fr);
            gap: 12px;
        }
        @media (max-width: 1000px) {
            .grid { grid-template-columns: 1fr; }
        }
        .card {
            background: var(--panel);
            border: 1px solid var(--line);
            border-radius: 16px;
            overflow: hidden;
            box-shadow: 0 20px 60px rgba(0,0,0,0.35);
        }
        .feed-wrap {
            position: relative;
            width: 100%;
            aspect-ratio: 16 / 9;
            background: #020617;
        }
        .feed {
            width: 100%;
            height: 100%;
            object-fit: contain;
            display: block;
        }
        .overlay {
            position: absolute;
            top: 12px;
            left: 12px;
            display: flex;
            align-items: center;
            gap: 8px;
            background: rgba(2,6,23,0.68);
            border: 1px solid var(--line);
            padding: 8px 12px;
            border-radius: 999px;
            font-size: 12px;
            backdrop-filter: blur(8px);
        }
        .panel {
            padding: 14px;
        }
        .controls {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 10px;
        }
        .btn {
            border: 1px solid var(--line);
            background: rgba(255,255,255,0.06);
            color: var(--text);
            padding: 12px 14px;
            border-radius: 12px;
            font-size: 14px;
            cursor: pointer;
        }
        .btn.primary {
            background: linear-gradient(135deg, var(--accent), #3b82f6);
            border-color: rgba(255,255,255,0.12);
        }
        .btn.good {
            background: linear-gradient(135deg, var(--accent2), #10b981);
            border-color: rgba(255,255,255,0.12);
        }
        .meta {
            margin-top: 12px;
            color: var(--muted);
            font-size: 13px;
            line-height: 1.5;
        }
        .photo {
            width: 100%;
            display: block;
            border-top: 1px solid var(--line);
        }
        .section-title {
            font-weight: 700;
            margin: 0 0 10px 0;
        }
    </style>
</head>
<body>
    <div class="wrap">
        <div class="top">
            <div>
                <h1>TermoCam Live Control</h1>
                <div class="sub">Camera preview auto-starts on load. Use this to frame the document and test capture.</div>
            </div>
            <div class="badge"><span class="dot"></span> LIVE</div>
        </div>
        <div class="grid">
            <div class="card">
                <div class="feed-wrap">
                    <img id="feed" class="feed" src="/preview.jpg" alt="Live camera preview">
                    <div class="overlay">Auto-refresh preview</div>
                </div>
            </div>
            <div class="card">
                <div class="panel">
                    <div class="section-title">Controls</div>
                    <div class="controls">
                        <button class="btn primary" onclick="refreshPreview()">Refresh Preview</button>
                        <button class="btn" onclick="stopPreview()">Pause</button>
                        <button class="btn good" onclick="capturePhoto()">Capture Photo</button>
                        <button class="btn" onclick="refreshPhoto()">Refresh Photo</button>
                    </div>
                    <div class="meta">
                        <div><strong>Preview:</strong> `/preview.jpg`</div>
                        <div><strong>Still:</strong> `/capture_photo`</div>
                        <div><strong>Latest:</strong> `/latest_photo`</div>
                    </div>
                </div>
                <img id="photo" class="photo" alt="Latest captured photo">
            </div>
        </div>
    </div>
    <script>
        const host = window.location.origin;
        const feed = document.getElementById('feed');
        const photo = document.getElementById('photo');
        let previewTimer = null;

        function refreshPreview() {
            feed.src = host + '/preview.jpg?t=' + Date.now();
            if (!previewTimer) {
                previewTimer = setInterval(() => {
                    feed.src = host + '/preview.jpg?t=' + Date.now();
                }, 1800);
            }
        }
        function stopPreview() {
            if (previewTimer) {
                clearInterval(previewTimer);
                previewTimer = null;
            }
        }
        function capturePhoto() {
            fetch(host + '/capture_photo', { method: 'POST' })
                .then(r => r.json())
                .then(data => {
                    if (data.status === 'success') {
                        refreshPhoto();
                    } else {
                        alert(data.message || 'Capture failed');
                    }
                })
                .catch(() => alert('Capture request failed'));
        }
        function refreshPhoto() {
            photo.src = host + '/latest_photo?t=' + Date.now();
        }
        window.addEventListener('load', () => {
            refreshPreview();
            refreshPhoto();
        });
    </script>
</body>
</html>
"""

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

def stop_stream_process():
    global stream_process
    with stream_process_lock:
        proc = stream_process
        stream_process = None
    if proc and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

def mjpeg_frames_from_stdout(stdout):
    buffer = b""
    while True:
        chunk = stdout.read(4096)
        if not chunk:
            break
        buffer += chunk
        while True:
            start = buffer.find(b"\xff\xd8")
            end = buffer.find(b"\xff\xd9", start + 2 if start != -1 else 0)
            if start != -1 and end != -1:
                frame = buffer[start:end + 2]
                buffer = buffer[end + 2:]
                yield frame
            else:
                if len(buffer) > 2 * 1024 * 1024:
                    buffer = buffer[-1024 * 1024:]
                break

def generate_frames():
    """Generator for MJPEG stream using rpicam-vid."""
    global stream_process, stop_stream_requested
    stop_stream_requested = False
    cmd = [
        "rpicam-vid",
        "--codec", "mjpeg",
        "--nopreview",
        "--framerate", str(STREAM_FPS),
        "--width", str(STREAM_WIDTH),
        "--height", str(STREAM_HEIGHT),
        "--timeout", "0",
        "--flush",
        "-o", "-"
    ]
    with stream_process_lock:
        stream_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0
        )
        proc = stream_process
    try:
        if not proc or not proc.stdout:
            return
        for frame_bytes in mjpeg_frames_from_stdout(proc.stdout):
            if stop_stream_requested:
                break
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
    finally:
        stop_stream_process()
                
@app.route('/video_feed')
def video_feed():
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/preview.jpg')
def preview_jpg():
    """Returns a quick still preview frame for the dashboard."""
    preview_path = "/home/ja/preview.jpg"
    with camera_lock:
        cmd = [
            "rpicam-still",
            "-o", preview_path,
            "--width", str(PREVIEW_WIDTH),
            "--height", str(PREVIEW_HEIGHT),
            "-t", "1",
            "--nopreview"
        ]
        try:
            subprocess.run(cmd, check=True)
            return send_file(preview_path, mimetype='image/jpeg')
        except subprocess.CalledProcessError as e:
            return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/')
def index():
    return render_template_string(CONTROL_PAGE)

@app.route('/capture_photo', methods=['POST'])
def capture_photo():
    """Takes a high-res photo using libcamera-still, pausing the stream."""
    global stop_stream_requested
    stop_stream_requested = True
    stop_stream_process()
    with camera_lock:
        print("Capturing high-res photo...")
        # Use the Pi OS camera CLI available on this system.
        cmd = [
            "rpicam-still",
            "-o", latest_photo_path,
            "--width", str(HIGH_RES_WIDTH),
            "--height", str(HIGH_RES_HEIGHT),
            "-t", "1000",
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
