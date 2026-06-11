import json
import os
import threading
import time
from datetime import datetime, timezone

import cv2

from pi.capture.frame_quality import should_accept_frame


class SweepSession:
    def __init__(self, session_id, camera_backend, config, data_dir):
        self.session_id = session_id
        self.camera = camera_backend
        self.config = config
        self.session_path = os.path.join(data_dir, "sessions", session_id)
        self.frames_path = os.path.join(self.session_path, "frames")
        self.rejected_path = os.path.join(self.session_path, "rejected")
        self.running = False
        self.accepted_frames = 0
        self.rejected_frames = 0
        self._thread = None
        self._stop = threading.Event()
        self._manifest = {
            "session_id": session_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "camera": camera_backend.get_metadata(),
            "capture_config": config,
            "frames": [],
            "rejected": [],
        }

    def _write_manifest(self):
        os.makedirs(self.session_path, exist_ok=True)
        path = os.path.join(self.session_path, "manifest.json")
        temp = f"{path}.tmp"
        with open(temp, "w") as handle:
            json.dump(self._manifest, handle, indent=2)
        os.replace(temp, path)

    def start(self):
        os.makedirs(self.frames_path, exist_ok=True)
        os.makedirs(self.rejected_path, exist_ok=True)
        self._write_manifest()
        self.running = True
        self._stop.clear()
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        return True

    def _capture_loop(self):
        previous = None
        interval = max(float(self.config.get("interval_ms", 150)) / 1000.0, 0.01)
        maximum = int(self.config.get("max_frames", 120))
        try:
            self.camera.start_preview()
            while not self._stop.is_set() and self.accepted_frames < maximum:
                image = self.camera.capture_array()
                result = should_accept_frame(image, previous, self.config)
                index = self.accepted_frames + self.rejected_frames + 1
                if result["accepted"]:
                    relative = f"frames/frame_{index:06d}.jpg"
                    cv2.imwrite(os.path.join(self.session_path, relative), image)
                    self._manifest["frames"].append({"filename": relative, **result})
                    previous = image
                    self.accepted_frames += 1
                else:
                    self._manifest["rejected"].append({"index": index, **result})
                    self.rejected_frames += 1
                self._write_manifest()
                time.sleep(interval)
        finally:
            self.camera.stop_preview()
            self.running = False
            self._write_manifest()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self.running = False
        self._write_manifest()
        return {
            "session_id": self.session_id,
            "status": "stopped",
            "accepted_frames": self.accepted_frames,
            "rejected_frames": self.rejected_frames,
        }

    def get_status(self):
        return {
            "running": self.running,
            "accepted_frames": self.accepted_frames,
            "rejected_frames": self.rejected_frames,
        }
