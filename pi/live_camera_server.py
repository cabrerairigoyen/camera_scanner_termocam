import os
import time
import json
import shutil
import threading
import subprocess
import yaml
import numpy as np
import cv2
import requests
from flask import Flask, Response, request, send_file, jsonify, render_template

from pi.capture.camera_backend import CameraBackend
from pi.capture.sweep_session import SweepSession
from pi.capture.uploader import zip_session, upload_session

app = Flask(__name__, template_folder='templates')

# Base Directories
script_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(script_dir)
data_dir = os.path.join(script_dir, "data")
sessions_dir = os.path.join(data_dir, "sessions")
os.makedirs(sessions_dir, exist_ok=True)

# Load configuration
config_path = os.path.join(script_dir, "config.yaml")
if not os.path.exists(config_path):
    config_path = os.path.join(script_dir, "config.example.yaml")

try:
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f) or {}
except Exception as e:
    print(f"Failed to load config: {e}. Using defaults.")
    config = {}

# Default camera configurations
STREAM_WIDTH = 640
STREAM_HEIGHT = 480
STREAM_FPS = 10

# Initialize global CameraBackend
camera_res = tuple(config.get("camera", {}).get("resolution", [2304, 1296]))
jpeg_quality = config.get("camera", {}).get("jpeg_quality", 90)
camera = CameraBackend(resolution=camera_res, jpeg_quality=jpeg_quality)

# Locks and State Machine
class CameraState:
    IDLE = "IDLE"
    STREAMING = "STREAMING"
    SWEEP_RUNNING = "SWEEP_RUNNING"
    CAPTURING_STILL = "CAPTURING_STILL"
    ERROR = "ERROR"

current_state = CameraState.IDLE
state_lock = threading.Lock()
camera_lock = threading.Lock()

# Sweep session state
active_session = None
active_session_id = None
last_error = None

# Stream control
stop_stream_requested = False
last_stream_activity = time.time()

# ----------------- Helper Functions -----------------

def get_cpu_temp() -> float:
    """Read CPU temperature using vcgencmd, falling back to 0.0 if unsupported."""
    try:
        res = subprocess.run(["vcgencmd", "measure_temp"], capture_output=True, text=True, check=True)
        # Result: "temp=52.3'C\n"
        temp_str = res.stdout.replace("temp=", "").replace("'C\n", "").strip()
        return float(temp_str)
    except Exception:
        return 45.0 # Mock temperature for non-Pi development environment

def get_free_disk_mb() -> int:
    """Returns the free space in MB on the data directory disk."""
    try:
        total, used, free = shutil.disk_usage(data_dir)
        return free // (1024 * 1024)
    except Exception:
        return 1024 # Mock free disk space

def get_warp_matrix(pts, width, height):
    """Calculates perspective transform matrix given 4 corners."""
    src_pts = np.array(pts, dtype=np.float32)
    dst_pts = np.array([
        [0, 0],
        [width - 1, 0],
        [width - 1, height - 1],
        [0, height - 1]
    ], dtype=np.float32)
    return cv2.getPerspectiveTransform(src_pts, dst_pts)

def apply_transform(frame) -> np.ndarray:
    """Applies rotation and perspective warp based on warp_points.json config."""
    if frame is None:
        return None
        
    # Read warp_points.json relative to parent folder
    warp_json_path = os.path.join(root_dir, "warp_points.json")
    transform_config = {"rotation": 0, "warp_points": None}
    
    if os.path.exists(warp_json_path):
        try:
            with open(warp_json_path, 'r') as f:
                transform_config["warp_points"] = json.load(f)
        except Exception as e:
            print(f"Failed to load warp points: {e}")
            
    # Apply rotation
    rot = transform_config.get('rotation', 0)
    if rot == 90:
        frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    elif rot == 180:
        frame = cv2.rotate(frame, cv2.ROTATE_180)
    elif rot == 270:
        frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
        
    # Apply perspective warp if calibrated
    warp_pts = transform_config.get('warp_points')
    if warp_pts and len(warp_pts) == 4:
        # Scale warp points if resolution differs
        # Since points are calibrated on full-res image, we scale them to the current frame size
        # We assume calibration image was standard full-res, but we query its width/height or use current frame's
        h, w = frame.shape[:2]
        matrix = get_warp_matrix(warp_pts, w, h)
        frame = cv2.warpPerspective(frame, matrix, (w, h))
        
    return frame

def stop_active_stream_if_running():
    """Stops the active streaming generator if running to release camera."""
    global current_state, stop_stream_requested
    
    with state_lock:
        if current_state != CameraState.STREAMING:
            return
            
    print("Stopping active alignment stream to claim camera lock...")
    stop_stream_requested = True
    
    # Wait for the stream loop to exit and release V4L2 device
    for _ in range(30):
        with state_lock:
            if current_state == CameraState.IDLE:
                break
        time.sleep(0.1)
        
    stop_stream_requested = False

# ----------------- REST Endpoints -----------------

@app.route('/')
def index_route():
    """Serves the premium camera control panel."""
    return render_template("camera_control.html")

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint reflecting system metrics."""
    temp = get_cpu_temp()
    free_disk = get_free_disk_mb()
    
    res = {
        "status": "ok" if temp < 82.0 and free_disk > 20 else "warning",
        "system_temp_c": temp,
        "free_disk_mb": free_disk,
        "state": current_state
    }
    return jsonify(res)

@app.route('/stream')
def video_feed():
    """MJPEG stream for camera alignment and positioning."""
    global current_state, stop_stream_requested, last_stream_activity
    
    # Check states
    with state_lock:
        if current_state == CameraState.SWEEP_RUNNING:
            return "Conflict: Camera is busy running a sweep session.", 409
        elif current_state == CameraState.CAPTURING_STILL:
            return "Conflict: Camera is busy capturing a still photo.", 409
            
        current_state = CameraState.STREAMING
        stop_stream_requested = False
        last_stream_activity = time.time()

    def generate():
        global current_state, last_stream_activity
        
        # Acquire camera lock
        with camera_lock:
            try:
                # Start stream at lower resolution for bandwidth/performance
                camera.start_preview()
                
                # Check for streaming timeout
                timeout_limit = config.get("limits", {}).get("max_stream_inactivity_sec", 300)
                
                while not stop_stream_requested:
                    now = time.time()
                    if now - last_stream_activity > timeout_limit:
                        print(f"Stream auto-closed due to {timeout_limit}s inactivity.")
                        break
                        
                    # Fast capture
                    frame = camera.capture_array()
                    frame = apply_transform(frame)
                    
                    # Encode to JPEG
                    ret, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
                    frame_bytes = buffer.tobytes()
                    
                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
                    time.sleep(1.0 / STREAM_FPS)
                    
            except Exception as e:
                print(f"Error yielding stream frames: {e}")
            finally:
                camera.stop_preview()
                with state_lock:
                    current_state = CameraState.IDLE
                    
    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/photo', methods=['GET'])
def photo():
    """Legacy capturing endpoint: takes a single corrected high-res photo."""
    global current_state, last_error
    
    # Stop any active streaming
    stop_active_stream_if_running()
    
    with state_lock:
        if current_state == CameraState.SWEEP_RUNNING:
            return jsonify({"status": "error", "message": "Camera is busy running a sweep capture."}), 409
        elif current_state == CameraState.CAPTURING_STILL:
            return jsonify({"status": "error", "message": "Camera is already capturing a still."}), 409
            
        current_state = CameraState.CAPTURING_STILL

    # Perform capture
    photo_path = os.path.join(data_dir, "autofocus_photo.jpg")
    corrected_path = os.path.join(data_dir, "documento_a4_corregido.jpg")
    
    with camera_lock:
        try:
            success = camera.capture_jpeg(photo_path)
            if not success:
                raise RuntimeError("Failed to capture still JPEG from sensor.")
                
            # Load captured frame and apply warp perspectives
            img = cv2.imread(photo_path)
            if img is not None:
                img_corrected = apply_transform(img)
                cv2.imwrite(corrected_path, img_corrected)
            else:
                shutil.copy(photo_path, corrected_path) # Fallback to uncorrected
                
            with state_lock:
                current_state = CameraState.IDLE
            return send_file(corrected_path, mimetype='image/jpeg')
            
        except Exception as e:
            last_error = str(e)
            with state_lock:
                current_state = CameraState.ERROR
            return jsonify({"status": "error", "message": last_error}), 500

@app.route('/process-highres', methods=['POST'])
def process_highres():
    """Takes a high-res photo and POSTs it directly to the Mac Server for AI extraction."""
    global current_state, last_error
    
    stop_active_stream_if_running()
    
    with state_lock:
        if current_state == CameraState.SWEEP_RUNNING:
            return jsonify({"status": "error", "message": "Camera is busy running a sweep capture."}), 409
        elif current_state == CameraState.CAPTURING_STILL:
            return jsonify({"status": "error", "message": "Camera is already capturing a still."}), 409
        current_state = CameraState.CAPTURING_STILL

    photo_path = os.path.join(data_dir, "autofocus_photo.jpg")
    
    with camera_lock:
        try:
            success = camera.capture_jpeg(photo_path)
            if not success:
                raise RuntimeError("Failed to capture still JPEG from sensor.")
            
            with state_lock:
                current_state = CameraState.IDLE
                
            # POST the captured photo to the Mac server
            # We use 127.0.0.1:8000 because of the reverse SSH tunnel from the Mac
            server_url = "http://127.0.0.1:8000/process-still"
            
            with open(photo_path, 'rb') as f:
                files = {'file': ('highres.jpg', f, 'image/jpeg')}
                print(f"Uploading high-res still to {server_url}...")
                response = requests.post(server_url, files=files, timeout=60)
                
            if response.status_code == 200:
                return jsonify(response.json()), 200
            else:
                return jsonify({"status": "error", "message": f"Server responded with {response.status_code}: {response.text}"}), 500

        except Exception as e:
            last_error = str(e)
            with state_lock:
                current_state = CameraState.ERROR
            return jsonify({"status": "error", "message": last_error}), 500

@app.route('/detect-page-preview', methods=['POST'])
def proxy_detect_preview():
    """Proxies the low-res preview frame to the Mac server."""
    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
        
    file = request.files['file']
    server_url = "http://127.0.0.1:8000/detect-page-preview"
    
    try:
        files = {'file': (file.filename, file.read(), file.content_type)}
        response = requests.post(server_url, files=files, timeout=2)
        return Response(response.content, status=response.status_code, mimetype=response.headers.get('Content-Type'))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/calibrate', methods=['GET', 'POST'])
def calibrate():
    """Captures unwarped raw photo for manual 4-corner calibration and runs autofocus once."""
    global current_state, last_error
    
    stop_active_stream_if_running()
    
    with state_lock:
        if current_state == CameraState.SWEEP_RUNNING:
            return jsonify({"status": "error", "message": "Camera is busy."}), 409
        current_state = CameraState.CAPTURING_STILL

    photo_path = os.path.join(data_dir, "autofocus_photo.jpg")
    
    with camera_lock:
        try:
            # Perform capture which automatically triggers focus
            success = camera.capture_jpeg(photo_path)
            with state_lock:
                current_state = CameraState.IDLE
                
            if success:
                return send_file(photo_path, mimetype='image/jpeg')
            else:
                return "Failed to capture calibration photo", 500
        except Exception as e:
            last_error = str(e)
            with state_lock:
                current_state = CameraState.ERROR
            return jsonify({"status": "error", "message": last_error}), 500

# ----------------- New Sweep Endpoints -----------------

@app.route('/sweep/start', methods=['POST'])
def sweep_start():
    """Starts a sweep capture session."""
    global current_state, active_session, active_session_id, last_error
    
    # Check disk and temperature limits
    min_disk = config.get("limits", {}).get("min_disk_space_mb", 50)
    free_disk = get_free_disk_mb()
    if free_disk < min_disk:
        return jsonify({"status": "error", "message": f"Low disk space: {free_disk}MB free, require {min_disk}MB."}), 400
        
    temp_limit = config.get("limits", {}).get("max_temp_c", 80.0)
    temp = get_cpu_temp()
    if temp > temp_limit:
        return jsonify({"status": "error", "message": f"Device temperature is too high: {temp}°C. Throttling active."}), 400

    # Stop any active streaming
    stop_active_stream_if_running()
    
    with state_lock:
        if current_state == CameraState.SWEEP_RUNNING:
            return jsonify({"status": "error", "message": "A sweep session is already active."}), 409
        elif current_state == CameraState.CAPTURING_STILL:
            return jsonify({"status": "error", "message": "Camera is busy capturing still/calibrating."}), 409
            
        current_state = CameraState.SWEEP_RUNNING

    # Retrieve parameters from request body
    data = request.json or {}
    
    # Architecture relies on reverse SSH tunnel, so we MUST use localhost:8000
    mac_ip_url = "http://127.0.0.1:8000/process-sweep"
    
    sweep_config = {
        "interval_ms": data.get("interval_ms", config.get("sweep", {}).get("interval_ms", 150)),
        "max_frames": data.get("max_frames", config.get("sweep", {}).get("max_frames", 120)),
        "sharpness_threshold": data.get("sharpness_threshold", config.get("sweep", {}).get("sharpness_threshold", 25.0)),
        "min_frame_difference": data.get("min_frame_difference", config.get("sweep", {}).get("min_frame_difference", 8.0)),
        "jpeg_quality": data.get("jpeg_quality", config.get("sweep", {}).get("jpeg_quality", 90)),
        "upload_after_capture": data.get("upload_after_capture", config.get("sweep", {}).get("upload_after_capture", False)),
        "server_url": mac_ip_url
    }
    
    # Update camera backend resolution if requested
    res_list = data.get("resolution")
    if res_list and len(res_list) == 2:
        camera.resolution = tuple(res_list)

    # Instantiate SweepSession
    session_id = f"sess_{int(time.time())}"
    active_session_id = session_id
    
    active_session = SweepSession(
        session_id=session_id,
        camera_backend=camera,
        config=sweep_config,
        data_dir=data_dir
    )
    
    # Acquire camera resource locks
    success = active_session.start()
    if success:
        return jsonify({
            "session_id": session_id,
            "status": "running"
        })
    else:
        with state_lock:
            current_state = CameraState.ERROR
            active_session = None
            active_session_id = None
        return jsonify({"status": "error", "message": "Failed to start capture thread."}), 500

@app.route('/sweep/stop', methods=['POST'])
def sweep_stop():
    """Stops the active sweep session."""
    global current_state, active_session, active_session_id
    
    if not active_session:
        return jsonify({"status": "error", "message": "No active sweep session to stop."}), 400
        
    res = active_session.stop()
    
    # Reset states
    with state_lock:
        current_state = CameraState.IDLE
        
    # Check if automatic upload is requested on completion
    upload_after = active_session.config.get("upload_after_capture", False)
    server_url = active_session.config.get("server_url", "")
    
    session_id = active_session_id
    
    if upload_after and server_url:
        print(f"Triggering auto-upload for session {session_id} to {server_url}...")
        # Run upload in background thread so endpoint completes quickly
        def bg_upload():
            time.sleep(0.5)
            zip_path = os.path.join(data_dir, f"{session_id}.zip")
            session_path = os.path.join(sessions_dir, session_id)
            if zip_session(session_path, zip_path):
                upload_session(zip_path, server_url)
                
        t = threading.Thread(target=bg_upload)
        t.daemon = True
        t.start()
        
    active_session = None
    active_session_id = None
    
    return jsonify(res)

@app.route('/sweep/status', methods=['GET'])
def sweep_status():
    """Returns the current sweep session status."""
    global current_state, active_session, active_session_id, last_error
    
    # Monitor streaming client activity to prevent lockups
    if current_state == CameraState.STREAMING:
        global last_stream_activity
        last_stream_activity = time.time()
        
    if active_session:
        stats = active_session.get_status()
        
        # Auto-cleanup lock if thread died or finished max_frames
        if not stats["running"]:
            with state_lock:
                if current_state == CameraState.SWEEP_RUNNING:
                    current_state = CameraState.IDLE
                    
        return jsonify({
            "status": "running" if stats["running"] else "stopped",
            "current_session_id": active_session_id,
            "accepted_frames": stats["accepted_frames"],
            "rejected_frames": stats["rejected_frames"],
            "last_error": None
        })
    else:
        return jsonify({
            "status": "idle" if current_state != CameraState.ERROR else "error",
            "current_session_id": None,
            "accepted_frames": 0,
            "rejected_frames": 0,
            "last_error": last_error
        })

@app.route('/sweep/sessions', methods=['GET'])
def sweep_sessions():
    """Lists completed and stored capture sessions."""
    sessions = []
    if os.path.exists(sessions_dir):
        for name in os.listdir(sessions_dir):
            sess_path = os.path.join(sessions_dir, name)
            manifest_path = os.path.join(sess_path, "manifest.json")
            if os.path.isdir(sess_path) and os.path.exists(manifest_path):
                try:
                    with open(manifest_path, 'r') as f:
                        m = json.load(f)
                    sessions.append({
                        "session_id": m.get("session_id"),
                        "created_at": m.get("created_at"),
                        "accepted_frames": len(m.get("frames", [])),
                        "rejected_frames": len(m.get("rejected", []))
                    })
                except Exception:
                    pass
                    
    # Sort by creation time decending
    sessions.sort(key=lambda x: x["created_at"] or "", reverse=True)
    return jsonify({"sessions": sessions})

@app.route('/sweep/<session_id>/manifest', methods=['GET'])
def sweep_manifest(session_id):
    """Returns the raw manifest file for a given session."""
    manifest_path = os.path.join(sessions_dir, session_id, "manifest.json")
    if os.path.exists(manifest_path):
        return send_file(manifest_path, mimetype='application/json')
    return jsonify({"status": "error", "message": "Manifest not found"}), 404

@app.route('/sweep/<session_id>/zip', methods=['GET'])
def sweep_zip(session_id):
    """Generates and downloads a ZIP package of a sweep session."""
    session_path = os.path.join(sessions_dir, session_id)
    if not os.path.exists(session_path):
        return jsonify({"status": "error", "message": "Session not found"}), 404
        
    zip_path = os.path.join(data_dir, f"{session_id}.zip")
    
    # Zip the folder
    success = zip_session(session_path, zip_path)
    if success:
        return send_file(zip_path, as_attachment=True, download_name=f"{session_id}.zip")
    return "Error compiling session zip", 500

@app.route('/sweep/<session_id>/upload', methods=['POST'])
def sweep_upload(session_id):
    """Triggers upload of a given session ZIP to the processing server."""
    session_path = os.path.join(sessions_dir, session_id)
    if not os.path.exists(session_path):
        return jsonify({"status": "error", "message": "Session not found"}), 404
        
    # Architecture relies on reverse SSH tunnel, MUST be localhost
    server_url = "http://127.0.0.1:8000/process-sweep"
        
    zip_path = os.path.join(data_dir, f"{session_id}.zip")
    
    # 1. Zip
    if not zip_session(session_path, zip_path):
        return jsonify({"status": "error", "message": "Failed to create session ZIP."}), 500
        
    # 2. Upload (blocks and returns direct server response)
    res = upload_session(zip_path, server_url)
    return jsonify(res)

@app.route('/sweep/<session_id>', methods=['DELETE'])
def sweep_delete(session_id):
    """Deletes a session and its frames to clear disk space."""
    session_path = os.path.join(sessions_dir, session_id)
    zip_path = os.path.join(data_dir, f"{session_id}.zip")
    
    if not os.path.exists(session_path):
        return jsonify({"status": "error", "message": "Session not found"}), 404
        
    try:
        shutil.rmtree(session_path)
        if os.path.exists(zip_path):
            os.remove(zip_path)
        return jsonify({"status": "success", "message": f"Deleted session {session_id}"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ----------------- Startup -----------------

if __name__ == '__main__':
    # Start Flask daemon on port 5000, allowing all cross-origin interfaces
    app.run(host='0.0.0.0', port=5000, threaded=True, debug=False)
