import cv2
import numpy as np


class CameraBackend:
    """Small Picamera-compatible facade with a synthetic development fallback."""

    def __init__(self, resolution=(2304, 1296), jpeg_quality=90):
        self.resolution = resolution
        self.jpeg_quality = jpeg_quality
        self._capture = None

    def start_preview(self):
        if self._capture is None:
            capture = cv2.VideoCapture(0)
            self._capture = capture if capture.isOpened() else None

    def stop_preview(self):
        if self._capture is not None:
            self._capture.release()
            self._capture = None

    def capture_array(self):
        if self._capture is not None:
            ok, frame = self._capture.read()
            if ok:
                return frame
        width, height = self.resolution
        return np.zeros((height, width, 3), dtype=np.uint8)

    def capture_jpeg(self, path):
        return bool(cv2.imwrite(path, self.capture_array(), [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality]))

    def autofocus_once(self):
        return True

    def lock_focus(self):
        return None

    def lock_exposure(self):
        return None

    def lock_awb(self):
        return None

    def get_metadata(self):
        return {"resolution": list(self.resolution)}
