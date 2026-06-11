import json
import logging
import mimetypes
import os
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests


LOGGER = logging.getLogger(__name__)


def _clean_base_url(value: str | None) -> str:
    base_url = (value or "").strip()
    if not base_url:
        return "http://127.0.0.1:8000"
    base_url = base_url.rstrip("/")
    for suffix in ("/process-sweep", "/process-still"):
        if base_url.endswith(suffix):
            base_url = base_url[: -len(suffix)]
    return base_url or "http://127.0.0.1:8000"


def _error(code: str, message: str, **details: Any) -> dict:
    payload = {"ok": False, "error": {"code": code, "message": message}}
    if details:
        payload["error"]["details"] = details
    return payload


def _unwrap_response(response: requests.Response, path: str) -> dict:
    if not response.ok:
        body = response.text[:500] if getattr(response, "text", "") else ""
        LOGGER.warning("TermoCam server rejected %s with HTTP %s", path, response.status_code)
        return _error(
            "BAD_RESPONSE",
            "Server returned an error response.",
            status_code=response.status_code,
            body=body,
        )
    try:
        data = response.json()
    except ValueError:
        body = response.text[:500] if getattr(response, "text", "") else ""
        LOGGER.warning("TermoCam server returned non-JSON for %s", path)
        return _error(
            "BAD_RESPONSE",
            "Server returned a non-JSON response.",
            status_code=response.status_code,
            body=body,
        )
    if not isinstance(data, dict):
        return _error("BAD_RESPONSE", "Server response was not a JSON object.", status_code=response.status_code)
    return {"ok": True, **data}


class DocumentClient:
    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        timeout_seconds: int | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = _clean_base_url(base_url or os.getenv("TERMOCAM_SERVER_BASE_URL"))
        self.token = token if token is not None else os.getenv("TERMOCAM_DEVICE_TOKEN", "")
        self.timeout_seconds = int(timeout_seconds or os.getenv("TERMOCAM_REQUEST_TIMEOUT_SECONDS", "60"))
        self.session = session or requests.Session()

    @classmethod
    def from_config(cls, config: dict | None = None) -> "DocumentClient":
        config = config or {}
        server_cfg = config.get("server", {}) if isinstance(config, dict) else {}
        document_cfg = config.get("document", {}) if isinstance(config, dict) else {}
        base_url = (
            os.getenv("TERMOCAM_SERVER_BASE_URL")
            or document_cfg.get("base_url")
            or server_cfg.get("base_url")
            or server_cfg.get("url")
        )
        timeout = os.getenv("TERMOCAM_REQUEST_TIMEOUT_SECONDS") or document_cfg.get("request_timeout_seconds")
        token = os.getenv("TERMOCAM_DEVICE_TOKEN") or document_cfg.get("device_token")
        return cls(base_url=base_url, token=token, timeout_seconds=timeout)

    def _headers(self, extra: dict | None = None) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if extra:
            headers.update(extra)
        return headers

    def _request(self, method: str, path: str, **kwargs) -> dict:
        url = urljoin(f"{self.base_url}/", path.lstrip("/"))
        kwargs.setdefault("timeout", self.timeout_seconds)
        headers = kwargs.pop("headers", None)
        kwargs["headers"] = self._headers(headers)
        try:
            response = self.session.request(method, url, **kwargs)
        except requests.Timeout:
            LOGGER.warning("TermoCam server request timed out: %s %s", method, path)
            return _error("SERVER_OFFLINE", "Request timed out.", reason="timeout", url=path)
        except requests.ConnectionError:
            LOGGER.warning("TermoCam server is unreachable: %s %s", method, path)
            return _error("SERVER_OFFLINE", "Server is unreachable.", reason="connection_error", url=path)
        except requests.RequestException as exc:
            LOGGER.warning("TermoCam server request failed: %s %s", method, path)
            return _error("NETWORK_ERROR", "Network request failed.", reason=exc.__class__.__name__, url=path)
        return _unwrap_response(response, path)

    def health_ready(self) -> dict:
        return self._request("GET", "/health/ready")

    def create_document(self, course: str | None = None, language: str | None = None, title: str | None = None) -> dict:
        result = self._request(
            "POST",
            "/documents",
            json={"course": course, "language": language, "title": title},
        )
        return result

    def upload_page(
        self,
        document_id: str,
        image_or_zip_path: str,
        page_number: int,
        capture_mode: str,
        replace_page_id: str | None = None,
    ) -> dict:
        path = Path(image_or_zip_path)
        if not path.exists():
            return _error("INVALID_UPLOAD", "Upload file does not exist.", path=path.name)
        mime = mimetypes.guess_type(path.name)[0] or ("application/zip" if path.suffix.lower() == ".zip" else "application/octet-stream")
        metadata = {
            "page_number": page_number,
            "capture_mode": capture_mode,
            "replace_page_id": replace_page_id,
        }
        try:
            with path.open("rb") as handle:
                result = self._request(
                    "POST",
                    f"/documents/{document_id}/pages",
                    files={"file": (path.name, handle, mime)},
                    data={"metadata_json": json.dumps(metadata)},
                )
        except OSError as exc:
            LOGGER.warning("Unable to open upload file %s", path.name)
            return _error("INVALID_UPLOAD", "Unable to read upload file.", reason=exc.__class__.__name__)
        if result.get("ok") is False:
            return result
        return result

    def finish_document(
        self,
        document_id: str,
        expected_page_count: int | None = None,
        solve: bool = True,
        answer_mode: str = "standard",
    ) -> dict:
        payload: dict[str, Any] = {"solve": solve, "answer_mode": answer_mode}
        if expected_page_count is not None:
            payload["expected_page_count"] = expected_page_count
        else:
            payload["expected_page_count"] = 1
        return self._request("POST", f"/documents/{document_id}/finish", json=payload)

    def get_document(self, document_id: str) -> dict:
        return self._request("GET", f"/documents/{document_id}")

    def get_events(self, document_id: str, after_sequence: int = 0) -> dict:
        return self._request("GET", f"/documents/{document_id}/events", params={"after_sequence": after_sequence})


def build_document_client(config: dict | None = None) -> DocumentClient:
    return DocumentClient.from_config(config)
