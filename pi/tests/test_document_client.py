import json

import requests

from pi.document_client import DocumentClient


class DummyResponse:
    def __init__(self, ok=True, status_code=200, payload=None, text="{}"):
        self.ok = ok
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text
        self.headers = {"content-type": "application/json"}

    def json(self):
        return self._payload


class DummySession:
    def __init__(self):
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if url.endswith("/documents"):
            return DummyResponse(payload={"document_id": "doc_1", "status": "CAPTURING", "next_page_number": 1})
        if url.endswith("/pages"):
            return DummyResponse(payload={"page_id": "page_1", "status": "QUALITY_CHECK_PENDING", "quality_job_id": "job_1"})
        return DummyResponse(payload={"ok": True})


def test_document_client_builds_requests(tmp_path):
    session = DummySession()
    client = DocumentClient(base_url="http://example.test", token="secret", timeout_seconds=3, session=session)
    create = client.create_document(course="GFN252", language="fr", title="Exam")
    assert create["ok"] is True
    method, url, kwargs = session.calls[0]
    assert method == "POST"
    assert url == "http://example.test/documents"
    assert kwargs["json"]["course"] == "GFN252"
    assert kwargs["headers"]["Authorization"] == "Bearer secret"

    file_path = tmp_path / "page.jpg"
    file_path.write_bytes(b"\xff\xd8\xff\xdb")
    upload = client.upload_page("doc_1", str(file_path), 1, "still")
    assert upload["ok"] is True
    method, url, kwargs = session.calls[1]
    assert method == "POST"
    assert url == "http://example.test/documents/doc_1/pages"
    assert "metadata_json" in kwargs["data"]
    metadata = json.loads(kwargs["data"]["metadata_json"])
    assert metadata["page_number"] == 1


def test_document_client_offline_handling():
    class OfflineSession:
        def request(self, *args, **kwargs):
            raise requests.ConnectionError("offline")

    client = DocumentClient(base_url="http://example.test", session=OfflineSession())
    response = client.create_document()
    assert response["ok"] is False
    assert response["error"]["code"] == "SERVER_OFFLINE"
