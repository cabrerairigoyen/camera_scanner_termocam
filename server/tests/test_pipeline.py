import os
import sys
import shutil
import tempfile
import json
import zipfile
import numpy as np
import cv2
from fastapi.testclient import TestClient

# Adjust path to import from server and pi
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from pi.capture.frame_quality import sharpness_laplacian, frame_difference, should_accept_frame
from pi.capture.sweep_session import SweepSession
from pi.capture.uploader import zip_session
from pi.live_camera_server import app as flask_app
from server.app import app as fastapi_app
from server.process_sweep import process_sweep_zip


# ----------------- Unit Tests for Quality Logic -----------------

def test_sharpness_metric():
    """Verify that the sharpness metric correctly distinguishes sharp vs blurry images."""
    # Create a sharp image with high-contrast text shapes
    sharp_img = np.zeros((400, 400, 3), dtype=np.uint8)
    cv2.rectangle(sharp_img, (50, 50), (350, 350), (255, 255, 255), -1)
    cv2.putText(sharp_img, "SHARP TEXT", (80, 200), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 4)
    
    # Create a blurred copy
    blurry_img = cv2.GaussianBlur(sharp_img, (25, 25), 0)
    
    val_sharp = sharpness_laplacian(sharp_img)
    val_blurry = sharpness_laplacian(blurry_img)
    
    print(f"Sharpness metric - Sharp: {val_sharp:.2f}, Blurry: {val_blurry:.2f}")
    assert val_sharp > val_blurry
    
    # Test decision logic
    config = {"sharpness_threshold": 100.0, "min_frame_difference": 5.0}
    
    # Sharp image should be accepted
    res_sharp = should_accept_frame(sharp_img, None, config)
    assert res_sharp["accepted"] is True
    
    # Blurry image with high threshold should be rejected as blur
    config_high = {"sharpness_threshold": 500.0}
    res_blurry = should_accept_frame(blurry_img, None, config_high)
    assert res_blurry["accepted"] is False
    assert res_blurry["reason"] == "blur"


def test_frame_difference_metric():
    """Verify that identical or near-identical frames are flagged as duplicates."""
    img1 = np.zeros((400, 400, 3), dtype=np.uint8)
    cv2.putText(img1, "FRAME A", (100, 200), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
    
    # Exact duplicate
    img2 = img1.copy()
    
    # Slightly offset image (representing camera movement)
    img3 = np.zeros((400, 400, 3), dtype=np.uint8)
    cv2.putText(img3, "FRAME A", (115, 200), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
    
    diff_dup = frame_difference(img1, img2)
    diff_move = frame_difference(img1, img3)
    
    print(f"Diff metric - Duplicate: {diff_dup:.2f}, Move: {diff_move:.2f}")
    assert diff_dup == 0.0
    assert diff_move > 0.0
    
    # Test duplicate decision
    config = {"sharpness_threshold": 10.0, "min_frame_difference": 1.0}
    
    # Duplicate image should be rejected
    res_dup = should_accept_frame(img2, img1, config)
    assert res_dup["accepted"] is False
    assert res_dup["reason"] == "duplicate"
    
    # Moved image should be accepted
    res_move = should_accept_frame(img3, img1, config)
    assert res_move["accepted"] is True


# ----------------- Staging Session Tests -----------------

class MockCameraBackend:
    def __init__(self):
        self.resolution = (640, 480)
        self.focus_mode = "auto"
        self.lens_position = "10.0"
        self.exposure_locked = False
        self.awb_locked = False
    def start_preview(self): pass
    def stop_preview(self): pass
    def capture_array(self):
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        cv2.putText(frame, "Mock Capture", (50, 100), cv2.FONT_HERSHEY_SIMPLEX, 1, (255,255,255), 2)
        return frame
    def capture_jpeg(self, path): return True
    def autofocus_once(self): return True
    def lock_focus(self): self.focus_mode = "locked"
    def lock_exposure(self): self.exposure_locked = True
    def lock_awb(self): self.awb_locked = True
    def get_metadata(self):
        return {
            "sensor": "IMX708", "resolution": list(self.resolution),
            "focus_mode": self.focus_mode, "lens_position": self.lens_position,
            "exposure_locked": self.exposure_locked, "awb_locked": self.awb_locked
        }


def test_session_and_zip_creation():
    """Verify that SweepSession correctly builds manifests and uploader compresses folders."""
    with tempfile.TemporaryDirectory() as temp_dir:
        mock_camera = MockCameraBackend()
        config = {
            "interval_ms": 100,
            "max_frames": 5,
            "sharpness_threshold": 10.0,
            "min_frame_difference": 4.0,
            "jpeg_quality": 85
        }
        
        session = SweepSession("test_session_123", mock_camera, config, data_dir=temp_dir)
        
        # Start and let it write manifest.json
        session.start()
        # Verify folders exist
        assert os.path.exists(session.frames_path)
        assert os.path.exists(session.rejected_path)
        
        # Stop session to finalize capture thread
        session.stop()
        
        manifest_path = os.path.join(session.session_path, "manifest.json")
        assert os.path.exists(manifest_path)
        
        with open(manifest_path, 'r') as f:
            manifest = json.load(f)
            
        assert manifest["session_id"] == "test_session_123"
        assert "camera" in manifest
        assert "capture_config" in manifest
        
        # Test ZIP archiving
        zip_output = os.path.join(temp_dir, "archive.zip")
        zip_ok = zip_session(session.session_path, zip_output)
        assert zip_ok is True
        assert os.path.exists(zip_output)
        
        # Verify ZIP contains manifest.json
        with zipfile.ZipFile(zip_output, 'r') as z:
            names = z.namelist()
            assert "manifest.json" in names


# ----------------- Integration Tests (Mocked API) -----------------

def test_pi_edge_server_locking():
    """Verify state transitions and resource blocks in Flask Edge server."""
    # Use Flask's testing utilities
    with flask_app.test_client() as client:
        # 1. Initially health should reflect IDLE
        res = client.get('/health')
        assert res.status_code == 200
        data = res.get_json()
        assert data["state"] == "IDLE"
        
        # 2. Start sweep
        res_start = client.post('/sweep/start', json={
            "interval_ms": 100, "max_frames": 5, "sharpness_threshold": 5.0
        })
        assert res_start.status_code == 200
        
        # 3. Status should now be active
        res_status = client.get('/sweep/status')
        data_status = res_status.get_json()
        assert data_status["status"] == "running"
        
        # 4. Starting a streaming view during active sweep should yield 409 Conflict
        res_stream = client.get('/stream')
        assert res_stream.status_code == 409
        
        # 5. Stop sweep
        res_stop = client.post('/sweep/stop')
        assert res_stop.status_code == 200
        
        # 6. Status should go back to idle
        res_status2 = client.get('/sweep/status')
        data_status2 = res_status2.get_json()
        assert data_status2["status"] == "idle"


def test_fastapi_server_reconstruction_failure():
    """Verify FastAPI processing endpoint accepts ZIP uploads and fails gracefully on un-stitchable frames."""
    client = TestClient(fastapi_app)
    
    # 1. Create a dummy zip session with random noise frames that cannot be stitched
    with tempfile.TemporaryDirectory() as temp_dir:
        session_dir = os.path.join(temp_dir, "sess_dummy")
        frames_dir = os.path.join(session_dir, "frames")
        os.makedirs(frames_dir)
        
        # Write dummy frames
        for i in range(1, 4):
            # Synthetic noise frame
            frame = np.random.randint(0, 255, (200, 200, 3), dtype=np.uint8)
            cv2.imwrite(os.path.join(frames_dir, f"frame_00000{i}.jpg"), frame)
            
        # Write manifest
        manifest = {
            "session_id": "sess_dummy",
            "camera": {"resolution": [200, 200]},
            "frames": [
                {"filename": "frames/frame_000001.jpg", "sharpness": 150.0},
                {"filename": "frames/frame_000002.jpg", "sharpness": 150.0},
                {"filename": "frames/frame_000003.jpg", "sharpness": 150.0}
            ]
        }
        with open(os.path.join(session_dir, "manifest.json"), 'w') as f:
            json.dump(manifest, f)
            
        zip_path = os.path.join(temp_dir, "dummy_session.zip")
        zip_session(session_dir, zip_path)
        
        # 2. Call process_sweep synchronously to test pipeline output
        jobs_dir = os.path.join(temp_dir, "jobs")
        report = process_sweep_zip(zip_path, "job_test_123", jobs_dir)
        
        # 3. Stitching must have failed
        assert report["stitching"]["status"] == "failed"
        assert report["stitching"]["method_used"] == "failed"
        assert len(report["stitching"]["warnings"]) > 0
        
        # 4. Upload ZIP to active FastAPI endpoint
        # Re-create ZIP file
        zip_session(session_dir, zip_path)
        with open(zip_path, 'rb') as f:
            response = client.post("/process-sweep", files={"file": ("dummy.zip", f, "application/zip")})
            
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "pending"
        assert "job_id" in data


if __name__ == "__main__":
    print("Running system validation tests...")
    test_funcs = [
        test_sharpness_metric,
        test_frame_difference_metric,
        test_session_and_zip_creation,
        test_pi_edge_server_locking,
        test_fastapi_server_reconstruction_failure
    ]
    
    success = True
    for func in test_funcs:
        print(f"\nExecuting: {func.__name__}...")
        try:
            func()
            print(f"PASSED: {func.__name__}")
        except Exception as e:
            print(f"FAILED: {func.__name__} with error: {e}")
            import traceback
            traceback.print_exc()
            success = False
            
    if success:
        print("\nAll validation tests PASSED successfully!")
        sys.exit(0)
    else:
        print("\nSome tests FAILED.")
        sys.exit(1)

