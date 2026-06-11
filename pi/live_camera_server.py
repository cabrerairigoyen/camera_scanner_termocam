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

from pi.audio_feedback import handle_audio_events
from pi.document_client import build_document_client
from pi.document_state_store import clear_state, load_state, save_state
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

# Document scan state
document_client = build_document_client(config)
current_document = {
    "document_id": None,
    "status": "IDLE",
    "next_page_number": 1,
    "last_event_sequence": 0,
    "pages": [],
}
document_lock = threading.Lock()
document_error = None
document_sync = {
    "server_reachable": False,
    "server_document_status": None,
    "needs_resync": False,
    "state_persisted": False,
    "last_error": None,
}

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
                warp_data = json.load(f)
                if isinstance(warp_data, dict):
                    transform_config["rotation"] = warp_data.get("rotation", 0)
                    transform_config["warp_points"] = warp_data.get("warp_points") or warp_data.get("points")
                else:
                    transform_config["warp_points"] = warp_data
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


def _document_state_snapshot():
    with document_lock:
        return {
            "document_id": current_document["document_id"],
            "status": current_document["status"],
            "next_page_number": current_document["next_page_number"],
            "last_event_sequence": current_document["last_event_sequence"],
            "pages": list(current_document["pages"]),
        }


def _document_status_from_server(server_status):
    return {
        "CAPTURING": "DOCUMENT_OPEN",
        "PROCESSING": "DOCUMENT_PROCESSING",
        "DONE": "DOCUMENT_DONE",
        "FAILED": "ERROR",
    }.get(server_status, server_status or "DOCUMENT_OPEN")


def _error_payload(code, message):
    return {"code": code, "message": message}


def _safe_nonnegative_int(value, default):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def _set_sync_state(**updates):
    with document_lock:
        document_sync.update(updates)


def _set_document_state(**updates):
    with document_lock:
        current_document.update(updates)


def _persist_document_state():
    global document_error
    state = _document_state_snapshot()
    state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        save_state(state)
    except (OSError, TypeError, ValueError) as exc:
        error = _error_payload("STATE_PERSIST_FAILED", f"Unable to persist Pi document state: {exc}")
        document_error = {"ok": False, "error": error}
        _set_sync_state(state_persisted=False, last_error=error)
        return False
    _set_sync_state(state_persisted=True)
    return True


def _reset_document_state(clear_persisted=False):
    global document_error
    with document_lock:
        current_document["document_id"] = None
        current_document["status"] = "IDLE"
        current_document["next_page_number"] = 1
        current_document["last_event_sequence"] = 0
        current_document["pages"] = []
    document_error = None
    _set_sync_state(
        server_reachable=False,
        server_document_status=None,
        needs_resync=False,
        state_persisted=False,
        last_error=None,
    )
    if clear_persisted:
        clear_state()


def _server_error(result, default_code="SERVER_OFFLINE"):
    error = result.get("error", {}) if isinstance(result, dict) else {}
    code = error.get("code", default_code)
    details = error.get("details", {}) if isinstance(error.get("details"), dict) else {}
    if details.get("status_code") == 404:
        return _error_payload("DOCUMENT_NOT_FOUND", "Document was not found on the TermoCam server.")
    if code in {"SERVER_OFFLINE", "NETWORK_ERROR"}:
        return _error_payload("SERVER_OFFLINE", "Cannot reach TermoCam server.")
    return _error_payload(code, error.get("message", "Document server request failed."))


def _pages_from_server(server_pages, local_pages):
    capture_modes = {
        page.get("page_id"): page.get("capture_mode")
        for page in local_pages
        if isinstance(page, dict) and page.get("page_id")
    }
    pages = []
    for page in server_pages if isinstance(server_pages, list) else []:
        if not isinstance(page, dict):
            continue
        pages.append({
            "page_id": page.get("page_id"),
            "page_number": page.get("page_number"),
            "capture_mode": capture_modes.get(page.get("page_id"), page.get("capture_mode", "unknown")),
            "status": page.get("status"),
        })
    return pages


def _apply_server_document(server_document, preserve_event_sequence=True):
    snapshot = _document_state_snapshot()
    pages = _pages_from_server(server_document.get("pages", []), snapshot["pages"])
    page_numbers = [page["page_number"] for page in pages if isinstance(page.get("page_number"), int)]
    next_page_number = server_document.get("next_page_number")
    if not isinstance(next_page_number, int) or next_page_number < 1:
        next_page_number = max(page_numbers, default=0) + 1
    updates = {
        "document_id": server_document.get("document_id") or snapshot["document_id"],
        "status": _document_status_from_server(server_document.get("status")),
        "next_page_number": next_page_number,
        "pages": pages,
    }
    if not preserve_event_sequence:
        updates["last_event_sequence"] = 0
    _set_document_state(**updates)
    _set_sync_state(
        server_reachable=True,
        server_document_status=server_document.get("status"),
        needs_resync=False,
        last_error=None,
    )
    _persist_document_state()
    return _document_state_snapshot()


def _sync_document(document_id, preserve_event_sequence=True):
    result = document_client.get_document(document_id)
    if not result.get("ok"):
        error = _server_error(result)
        _set_sync_state(
            server_reachable=error["code"] != "SERVER_OFFLINE",
            needs_resync=True,
            last_error=error,
        )
        return None, error
    server_document = {key: value for key, value in result.items() if key != "ok"}
    return _apply_server_document(server_document, preserve_event_sequence), None


def recover_document_state():
    stored = load_state()
    if not stored:
        return _document_state_snapshot()
    _set_document_state(
        document_id=stored.get("document_id"),
        status=stored.get("status", "IDLE"),
        next_page_number=max(1, _safe_nonnegative_int(stored.get("next_page_number"), 1)),
        last_event_sequence=_safe_nonnegative_int(stored.get("last_event_sequence"), 0),
        pages=stored.get("pages", []) if isinstance(stored.get("pages"), list) else [],
    )
    _set_sync_state(state_persisted=True)
    if not stored.get("document_id"):
        return _document_state_snapshot()
    recovered, error = _sync_document(stored["document_id"])
    if error:
        if error["code"] == "SERVER_OFFLINE":
            _set_document_state(status="SERVER_OFFLINE")
        else:
            _set_document_state(status="ERROR")
        _persist_document_state()
    return recovered or _document_state_snapshot()


def _normalize_server_error(result: dict, fallback_code: str = "SERVER_OFFLINE", fallback_status: int = 503):
    error = result.get("error", {}) if isinstance(result, dict) else {}
    code = error.get("code", fallback_code)
    message = error.get("message", "Document server request failed.")
    status_code = error.get("details", {}).get("status_code", fallback_status) if isinstance(error.get("details"), dict) else fallback_status
    if code in {"BAD_RESPONSE"}:
        status_code = 502
    elif code in {"INVALID_UPLOAD"}:
        status_code = 400
    return jsonify({"status": "error", "error": {"code": code, "message": message, "details": error.get("details", {})}}), status_code


def _capture_document_still_file(page_number: int) -> str:
    photo_path = os.path.join(data_dir, "autofocus_photo.jpg")
    corrected_path = os.path.join(data_dir, f"document_page_{page_number}.jpg")
    success = camera.capture_jpeg(photo_path)
    if not success:
        raise RuntimeError("Failed to capture still JPEG from sensor.")
    img = cv2.imread(photo_path)
    if img is not None:
        img_corrected = apply_transform(img)
        cv2.imwrite(corrected_path, img_corrected)
        return corrected_path
    shutil.copy(photo_path, corrected_path)
    return corrected_path


def _start_sweep_session(data=None, document_mode=False):
    global current_state, active_session, active_session_id, last_error
    data = data or {}
    min_disk = config.get("limits", {}).get("min_disk_space_mb", 50)
    free_disk = get_free_disk_mb()
    if free_disk < min_disk:
        return {"status": "error", "message": f"Low disk space: {free_disk}MB free, require {min_disk}MB."}, 400

    temp_limit = config.get("limits", {}).get("max_temp_c", 80.0)
    temp = get_cpu_temp()
    if temp > temp_limit:
        return {"status": "error", "message": f"Device temperature is too high: {temp}°C. Throttling active."}, 400

    stop_active_stream_if_running()

    with state_lock:
        if current_state == CameraState.SWEEP_RUNNING:
            return {"status": "error", "message": "A sweep session is already active."}, 409
        elif current_state == CameraState.CAPTURING_STILL:
            return {"status": "error", "message": "Camera is busy capturing still/calibrating."}, 409
        current_state = CameraState.SWEEP_RUNNING

    sweep_config = {
        "interval_ms": data.get("interval_ms", config.get("sweep", {}).get("interval_ms", 150)),
        "max_frames": data.get("max_frames", config.get("sweep", {}).get("max_frames", 120)),
        "sharpness_threshold": data.get("sharpness_threshold", config.get("sweep", {}).get("sharpness_threshold", 25.0)),
        "min_frame_difference": data.get("min_frame_difference", config.get("sweep", {}).get("min_frame_difference", 8.0)),
        "jpeg_quality": data.get("jpeg_quality", config.get("sweep", {}).get("jpeg_quality", 90)),
        "upload_after_capture": data.get("upload_after_capture", config.get("sweep", {}).get("upload_after_capture", False)) if not document_mode else False,
        "server_url": None if document_mode else "http://127.0.0.1:8000/process-sweep",
    }

    res_list = data.get("resolution")
    if res_list and len(res_list) == 2:
        camera.resolution = tuple(res_list)

    session_id = f"sess_{int(time.time())}"
    session = SweepSession(
        session_id=session_id,
        camera_backend=camera,
        config=sweep_config,
        data_dir=data_dir,
    )
    active_session_id = session_id
    active_session = session
    success = session.start()
    if not success:
        with state_lock:
            current_state = CameraState.ERROR
            active_session = None
            active_session_id = None
        return {"status": "error", "message": "Failed to start capture thread."}, 500

    return {"session_id": session_id, "status": "running"}, 200


def _stop_sweep_session():
    global current_state, active_session, active_session_id, document_error
    if not active_session:
        return {"status": "error", "message": "No active sweep session to stop."}, 400, None

    res = active_session.stop()
    session_id = active_session_id
    session_path = os.path.join(sessions_dir, session_id) if session_id else None
    with state_lock:
        current_state = CameraState.IDLE
    active_session = None
    active_session_id = None
    document_error = None
    return res, 200, session_path


recover_document_state()

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


# ----------------- Document-Aware Endpoints -----------------

@app.route('/document/start', methods=['POST'])
def document_start():
    global document_error
    payload = request.get_json(silent=True) or {}
    existing = _document_state_snapshot()
    if existing["document_id"] and existing["status"] != "IDLE":
        return jsonify({
            "document_id": existing["document_id"],
            "status": "DOCUMENT_OPEN",
            "next_page_number": existing["next_page_number"],
        })

    requested_document_id = payload.get("document_id")
    if requested_document_id:
        _set_document_state(
            document_id=requested_document_id,
            status="DOCUMENT_OPEN",
            next_page_number=int(payload.get("next_page_number", 1) or 1),
            last_event_sequence=0,
            pages=[],
        )
        _set_sync_state(needs_resync=True, last_error=None)
        _persist_document_state()
        return jsonify({
            "document_id": requested_document_id,
            "status": "DOCUMENT_OPEN",
            "next_page_number": int(payload.get("next_page_number", 1) or 1),
        })

    result = document_client.create_document(
        course=payload.get("course") or config.get("document", {}).get("default_course") or None,
        language=payload.get("language") or config.get("document", {}).get("default_language") or "fr",
        title=payload.get("title"),
    )
    if not result.get("ok"):
        document_error = result
        error = _server_error(result)
        _set_sync_state(
            server_reachable=error["code"] != "SERVER_OFFLINE",
            needs_resync=True,
            last_error=error,
        )
        return _normalize_server_error(result)

    document_id = result["document_id"]
    _set_document_state(
        document_id=document_id,
        status="DOCUMENT_OPEN",
        next_page_number=int(result.get("next_page_number", 1) or 1),
        last_event_sequence=0,
        pages=[],
    )
    _set_sync_state(
        server_reachable=True,
        server_document_status=result.get("status", "CAPTURING"),
        needs_resync=False,
        last_error=None,
    )
    _persist_document_state()
    return jsonify({
        "document_id": document_id,
        "status": "DOCUMENT_OPEN",
        "next_page_number": current_document["next_page_number"],
    })


@app.route('/document/status', methods=['GET'])
def document_status():
    snapshot = _document_state_snapshot()
    if snapshot["document_id"]:
        _sync_document(snapshot["document_id"])
    else:
        server_health = document_client.health_ready()
        if server_health.get("ok"):
            _set_sync_state(server_reachable=True, needs_resync=False, last_error=None)
        else:
            error = _server_error(server_health)
            _set_sync_state(server_reachable=False, needs_resync=False, last_error=error)
    snapshot = _document_state_snapshot()
    with document_lock:
        sync = dict(document_sync)
    return jsonify({
        "local_status": snapshot["status"],
        "document_id": snapshot["document_id"],
        "next_page_number": snapshot["next_page_number"],
        "last_event_sequence": snapshot["last_event_sequence"],
        "pages": snapshot["pages"],
        "server_reachable": sync["server_reachable"],
        "server_document_status": sync["server_document_status"],
        "needs_resync": sync["needs_resync"],
        "state_persisted": sync["state_persisted"],
        "last_error": sync["last_error"],
    })


@app.route('/document/capture-still', methods=['POST'])
def document_capture_still():
    global document_error, current_state
    payload = request.get_json(silent=True) or {}
    snapshot = _document_state_snapshot()
    if not snapshot["document_id"]:
        return jsonify({"status": "error", "error": {"code": "JOB_CONFLICT", "message": "No open document."}}), 409

    page_number = int(payload.get("page_number") or snapshot["next_page_number"] or 1)
    replace_page_id = payload.get("replace_page_id")

    stop_active_stream_if_running()
    with state_lock:
        if current_state == CameraState.SWEEP_RUNNING:
            return jsonify({"status": "error", "message": "Camera is busy running a sweep capture."}), 409
        current_state = CameraState.CAPTURING_STILL

    try:
        image_path = _capture_document_still_file(page_number)
        with document_lock:
            current_document["status"] = "UPLOADING_PAGE"
        result = document_client.upload_page(
            snapshot["document_id"],
            image_path,
            page_number=page_number,
            capture_mode="still",
            replace_page_id=replace_page_id,
        )
        if not result.get("ok"):
            document_error = result
            error = _server_error(result)
            _set_document_state(status=snapshot["status"])
            _set_sync_state(
                server_reachable=error["code"] != "SERVER_OFFLINE",
                needs_resync=True,
                last_error=error,
            )
            return _normalize_server_error(result)
        page_id = result["page_id"]
        quality_job_id = result["quality_job_id"]
        _set_document_state(
            status="WAITING_QUALITY",
            next_page_number=page_number + 1,
            pages=snapshot["pages"] + [{
                "page_id": page_id,
                "page_number": page_number,
                "status": result.get("status", "QUALITY_CHECK_PENDING"),
                "capture_mode": "still",
            }],
        )
        _set_sync_state(server_reachable=True, needs_resync=False, last_error=None)
        _persist_document_state()
        return jsonify({
            "document_id": snapshot["document_id"],
            "page_id": page_id,
            "page_number": page_number,
            "status": result.get("status", "QUALITY_CHECK_PENDING"),
            "quality_job_id": quality_job_id,
        })
    except Exception as exc:
        document_error = {"ok": False, "error": {"code": "INTERNAL_ERROR", "message": str(exc)}}
        _set_document_state(status="ERROR")
        return jsonify({"status": "error", "error": {"code": "INTERNAL_ERROR", "message": str(exc)}}), 500
    finally:
        with state_lock:
            if current_state == CameraState.CAPTURING_STILL:
                current_state = CameraState.IDLE


@app.route('/document/capture-sweep/start', methods=['POST'])
def document_capture_sweep_start():
    snapshot = _document_state_snapshot()
    if not snapshot["document_id"]:
        return jsonify({"status": "error", "error": {"code": "JOB_CONFLICT", "message": "No open document."}}), 409
    payload = request.get_json(silent=True) or {}
    body, status = _start_sweep_session(payload, document_mode=True)
    if status != 200:
        return jsonify(body), status
    _set_document_state(status="CAPTURING_PAGE")
    return jsonify({
        "document_id": snapshot["document_id"],
        "status": "CAPTURING_PAGE",
        "session_id": body["session_id"],
        "page_number": snapshot["next_page_number"],
    })


@app.route('/document/capture-sweep/stop', methods=['POST'])
def document_capture_sweep_stop():
    global document_error
    snapshot = _document_state_snapshot()
    if not snapshot["document_id"]:
        return jsonify({"status": "error", "error": {"code": "JOB_CONFLICT", "message": "No open document."}}), 409
    res, status, session_path = _stop_sweep_session()
    if status != 200:
        return jsonify(res), status

    session_id = res.get("session_id")
    if not session_id or not session_path:
        _set_document_state(status="ERROR")
        return jsonify({"status": "error", "error": {"code": "INTERNAL_ERROR", "message": "Sweep session could not be compiled."}}), 500

    zip_path = os.path.join(data_dir, f"{session_id}.zip")
    if not zip_session(session_path, zip_path):
        _set_document_state(status="ERROR")
        return jsonify({"status": "error", "error": {"code": "INVALID_UPLOAD", "message": "Failed to package sweep session."}}), 500

    page_number = snapshot["next_page_number"]
    upload_result = document_client.upload_page(
        snapshot["document_id"],
        zip_path,
        page_number=page_number,
        capture_mode="sweep",
        replace_page_id=None,
    )
    if not upload_result.get("ok"):
        document_error = upload_result
        error = _server_error(upload_result)
        _set_sync_state(
            server_reachable=error["code"] != "SERVER_OFFLINE",
            needs_resync=True,
            last_error=error,
        )
        return _normalize_server_error(upload_result)

    page_id = upload_result["page_id"]
    _set_document_state(
        status="WAITING_QUALITY",
        next_page_number=page_number + 1,
        pages=snapshot["pages"] + [{
            "page_id": page_id,
            "page_number": page_number,
            "status": upload_result.get("status", "QUALITY_CHECK_PENDING"),
            "capture_mode": "sweep",
        }],
    )
    _set_sync_state(server_reachable=True, needs_resync=False, last_error=None)
    _persist_document_state()
    return jsonify({
        "document_id": snapshot["document_id"],
        "page_id": page_id,
        "page_number": page_number,
        "status": upload_result.get("status", "QUALITY_CHECK_PENDING"),
        "quality_job_id": upload_result.get("quality_job_id"),
    })


@app.route('/document/finish', methods=['POST'])
def document_finish():
    global document_error
    snapshot = _document_state_snapshot()
    if not snapshot["document_id"]:
        return jsonify({"status": "error", "error": {"code": "JOB_CONFLICT", "message": "No open document."}}), 409

    payload = request.get_json(silent=True) or {}
    result = document_client.finish_document(
        snapshot["document_id"],
        expected_page_count=payload.get("expected_page_count"),
        solve=payload.get("solve", True),
        answer_mode=payload.get("answer_mode", "standard"),
    )
    if not result.get("ok"):
        document_error = result
        error = _server_error(result)
        _set_sync_state(
            server_reachable=error["code"] != "SERVER_OFFLINE",
            needs_resync=True,
            last_error=error,
        )
        return _normalize_server_error(result)

    server_status = result.get("status", "PROCESSING")
    local_status = "DOCUMENT_PROCESSING" if server_status == "PROCESSING" else "DOCUMENT_DONE"
    _set_document_state(status=local_status)
    _set_sync_state(
        server_reachable=True,
        server_document_status=server_status,
        needs_resync=False,
        last_error=None,
    )
    _persist_document_state()
    return jsonify({
        "document_id": snapshot["document_id"],
        "status": local_status,
        "job_id": result.get("job_id"),
    })


@app.route('/document/events', methods=['GET'])
def document_events():
    snapshot = _document_state_snapshot()
    if not snapshot["document_id"]:
        return jsonify({
            "document_id": None,
            "events": [],
            "next_after_sequence": snapshot["last_event_sequence"],
        })

    response = document_client.get_events(snapshot["document_id"], after_sequence=snapshot["last_event_sequence"])
    if not response.get("ok"):
        error = _server_error(response)
        _set_sync_state(
            server_reachable=error["code"] != "SERVER_OFFLINE",
            needs_resync=True,
            last_error=error,
        )
        return _normalize_server_error(response)
    events = response.get("events", [])
    next_after_sequence = response.get("next_after_sequence", snapshot["last_event_sequence"])
    if isinstance(events, list) and events:
        handle_audio_events(events)
    _set_document_state(last_event_sequence=next_after_sequence)
    _set_sync_state(server_reachable=True, needs_resync=False, last_error=None)
    _persist_document_state()
    return jsonify({
        "document_id": snapshot["document_id"],
        "events": events,
        "next_after_sequence": next_after_sequence,
    })


@app.route('/document/attach', methods=['POST'])
def document_attach():
    payload = request.get_json(silent=True) or {}
    document_id = payload.get("document_id")
    if not isinstance(document_id, str) or not document_id.strip():
        return jsonify({
            "status": "error",
            "error": _error_payload("INVALID_REQUEST", "document_id is required."),
        }), 400
    previous = _document_state_snapshot()
    _set_document_state(
        document_id=document_id.strip(),
        status="DOCUMENT_OPEN",
        next_page_number=1,
        last_event_sequence=0,
        pages=[],
    )
    state, error = _sync_document(document_id.strip(), preserve_event_sequence=False)
    if error:
        _set_document_state(**previous)
        status_code = 404 if error["code"] == "DOCUMENT_NOT_FOUND" else 503
        return jsonify({"status": "error", "error": error}), status_code
    return jsonify(state)


@app.route('/document/resync', methods=['POST'])
def document_resync():
    snapshot = _document_state_snapshot()
    if not snapshot["document_id"]:
        return jsonify({
            "status": "error",
            "error": _error_payload("JOB_CONFLICT", "No local document to resync."),
        }), 409
    state, error = _sync_document(snapshot["document_id"])
    if error:
        status_code = 404 if error["code"] == "DOCUMENT_NOT_FOUND" else 503
        return jsonify({"status": "error", "error": error}), status_code
    return jsonify(state)


@app.route('/document/reset', methods=['POST'])
def document_reset():
    global current_state
    _reset_document_state(clear_persisted=True)
    with state_lock:
        if current_state == CameraState.CAPTURING_STILL:
            current_state = CameraState.IDLE
    return jsonify({
        "status": "IDLE",
        "document_id": None,
        "next_page_number": 1,
        "deleted_server_document": False,
    })

# ----------------- New Sweep Endpoints -----------------

@app.route('/sweep/start', methods=['POST'])
def sweep_start():
    """Starts a sweep capture session."""
    payload = request.json or {}
    body, status = _start_sweep_session(payload, document_mode=False)
    return jsonify(body), status

@app.route('/sweep/stop', methods=['POST'])
def sweep_stop():
    """Stops the active sweep session."""
    res, status, session_path = _stop_sweep_session()
    if status != 200:
        return jsonify(res), status

    payload = request.get_json(silent=True) or {}
    upload_after = bool(payload.get("upload_after_capture"))
    server_url = payload.get("server_url")
    session_id = res.get("session_id")

    if upload_after and server_url and session_id:
        print(f"Triggering auto-upload for session {session_id} to {server_url}...")

        def bg_upload():
            time.sleep(0.5)
            zip_path = os.path.join(data_dir, f"{session_id}.zip")
            if session_path and zip_session(session_path, zip_path):
                upload_session(zip_path, server_url)

        t = threading.Thread(target=bg_upload)
        t.daemon = True
        t.start()

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
