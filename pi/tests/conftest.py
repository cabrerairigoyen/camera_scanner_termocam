import importlib

import cv2
import numpy as np
import pytest


class FakeCamera:
    def start_preview(self):
        return None

    def stop_preview(self):
        return None

    def capture_jpeg(self, path):
        image = np.zeros((32, 32, 3), dtype=np.uint8)
        image[:, :] = (0, 255, 0)
        return bool(cv2.imwrite(path, image))

    def capture_array(self):
        return np.zeros((32, 32, 3), dtype=np.uint8)

    def get_metadata(self):
        return {"resolution": [32, 32]}


class FakeDocumentClient:
    def __init__(self):
        self.calls = []
        self.create_response = {"ok": True, "document_id": "doc_test", "status": "CAPTURING", "next_page_number": 1}
        self.upload_response = {"ok": True, "page_id": "page_test", "status": "QUALITY_CHECK_PENDING", "quality_job_id": "job_quality"}
        self.finish_response = {"ok": True, "document_id": "doc_test", "status": "PROCESSING", "job_id": "job_finish"}
        self.events_response = {"ok": True, "events": [], "next_after_sequence": 0}
        self.health_response = {"ok": True, "status": "ok"}
        self.document_response = {"ok": True, "document_id": "doc_test"}

    def create_document(self, **kwargs):
        self.calls.append(("create_document", kwargs))
        return self.create_response

    def upload_page(self, *args, **kwargs):
        self.calls.append(("upload_page", args, kwargs))
        return self.upload_response

    def finish_document(self, *args, **kwargs):
        self.calls.append(("finish_document", args, kwargs))
        return self.finish_response

    def get_document(self, *args, **kwargs):
        self.calls.append(("get_document", args, kwargs))
        return self.document_response

    def get_events(self, *args, **kwargs):
        self.calls.append(("get_events", args, kwargs))
        return self.events_response

    def health_ready(self):
        self.calls.append(("health_ready",))
        return self.health_response


@pytest.fixture
def live_server(monkeypatch, tmp_path):
    monkeypatch.setenv("TERMOCAM_PI_STATE_PATH", str(tmp_path / "document_state.json"))
    module = importlib.import_module("pi.live_camera_server")
    module = importlib.reload(module)
    module.camera = FakeCamera()
    module.document_client = FakeDocumentClient()
    module._reset_document_state(clear_persisted=True)
    module.active_session = None
    module.active_session_id = None
    module.current_state = module.CameraState.IDLE
    module.last_error = None
    yield module
