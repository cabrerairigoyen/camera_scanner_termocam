import importlib

import pytest
import requests

from pi import controller_commands


class StubResponse:
    def __init__(
        self,
        *,
        ok=True,
        status_code=200,
        payload=None,
        text="",
        content_type="application/json",
        json_error=None,
    ):
        self.ok = ok
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = {"content-type": content_type}
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise self._json_error
        return self._payload


@pytest.mark.parametrize(
    ("command", "method", "path"),
    [
        ("A", "POST", "/document/capture-still"),
        ("B", "POST", "/document/capture-sweep/start"),
        ("C", "POST", "/document/capture-sweep/stop"),
        ("D", "POST", "/document/finish"),
        ("0", "GET", "/health"),
        ("#", "GET", "/document/events"),
        ("*", "GET", "/document/status"),
    ],
)
def test_valid_commands_use_expected_pi_route(monkeypatch, command, method, path):
    calls = []

    def fake_request(actual_method, url, **kwargs):
        calls.append((actual_method, url, kwargs))
        return StubResponse(payload={"status": "ok"})

    monkeypatch.setattr(controller_commands.requests, "request", fake_request)

    result = controller_commands.handle_command(command)

    assert calls == [
        (method, f"http://127.0.0.1:5000{path}", {"timeout": 10})
    ]
    assert result == {
        "ok": True,
        "command": command,
        "method": method,
        "path": path,
        "status_code": 200,
        "response": {"status": "ok"},
    }


def test_lowercase_command_is_normalized(monkeypatch):
    monkeypatch.setattr(
        controller_commands.requests,
        "request",
        lambda *args, **kwargs: StubResponse(payload={"page_id": "page_1"}),
    )

    result = controller_commands.handle_command(" a ")

    assert result["ok"] is True
    assert result["command"] == "A"
    assert result["path"] == "/document/capture-still"


@pytest.mark.parametrize("command", ["Z", "1", "AB"])
def test_unknown_command_returns_clean_error_without_http_request(monkeypatch, command):
    def fail_request(*args, **kwargs):
        pytest.fail("unknown commands must not make an HTTP request")

    monkeypatch.setattr(controller_commands.requests, "request", fail_request)

    result = controller_commands.handle_command(command)

    assert result == {
        "ok": False,
        "command": command,
        "method": None,
        "path": None,
        "error": {
            "code": "UNKNOWN_COMMAND",
            "message": "Unsupported command.",
        },
    }


@pytest.mark.parametrize("command", ["", "   ", None])
def test_empty_command_returns_clean_error(monkeypatch, command):
    monkeypatch.setattr(
        controller_commands.requests,
        "request",
        lambda *args, **kwargs: pytest.fail("empty commands must not make an HTTP request"),
    )

    result = controller_commands.handle_command(command)

    assert result["ok"] is False
    assert result["command"] == ""
    assert result["path"] is None
    assert result["error"]["code"] == "UNKNOWN_COMMAND"


@pytest.mark.parametrize(
    ("exception", "code", "message"),
    [
        (requests.ConnectionError("offline"), "SERVER_OFFLINE", "Pi server is offline."),
        (requests.Timeout("slow"), "TIMEOUT", "Command timed out."),
        (requests.RequestException("broken"), "NETWORK_ERROR", "broken"),
    ],
)
def test_request_failure_returns_clean_error(monkeypatch, exception, code, message):
    def fail_request(*args, **kwargs):
        raise exception

    monkeypatch.setattr(controller_commands.requests, "request", fail_request)

    result = controller_commands.handle_command("#")

    assert result == {
        "ok": False,
        "command": "#",
        "method": "GET",
        "path": "/document/events",
        "error": {"code": code, "message": message},
    }


def test_invalid_json_response_is_safe(monkeypatch):
    monkeypatch.setattr(
        controller_commands.requests,
        "request",
        lambda *args, **kwargs: StubResponse(
            payload=None,
            text="<invalid-json>",
            json_error=ValueError("invalid JSON"),
        ),
    )

    result = controller_commands.handle_command("*")

    assert result == {
        "ok": False,
        "command": "*",
        "method": "GET",
        "path": "/document/status",
        "status_code": 200,
        "error": {
            "code": "INVALID_RESPONSE",
            "message": "Pi server returned invalid JSON.",
        },
    }


def test_non_json_response_is_returned_as_text(monkeypatch):
    monkeypatch.setattr(
        controller_commands.requests,
        "request",
        lambda *args, **kwargs: StubResponse(
            payload=None,
            text="camera ready",
            content_type="text/plain",
        ),
    )

    result = controller_commands.handle_command("0")

    assert result["ok"] is True
    assert result["response"] == "camera ready"


def test_http_error_preserves_response_for_callers(monkeypatch):
    monkeypatch.setattr(
        controller_commands.requests,
        "request",
        lambda *args, **kwargs: StubResponse(
            ok=False,
            status_code=409,
            payload={"status": "error", "error": {"code": "JOB_CONFLICT"}},
        ),
    )

    result = controller_commands.handle_command("A")

    assert result["ok"] is False
    assert result["status_code"] == 409
    assert result["path"] == "/document/capture-still"
    assert result["response"]["error"]["code"] == "JOB_CONFLICT"


def test_configured_pi_base_url_is_respected(monkeypatch):
    calls = []
    try:
        monkeypatch.setenv("TERMOCAM_PI_BASE_URL", "http://pi.example:5050/root/")
        module = importlib.reload(controller_commands)
        monkeypatch.setattr(
            module.requests,
            "request",
            lambda method, url, **kwargs: (
                calls.append((method, url, kwargs))
                or StubResponse(payload={"status": "ok"})
            ),
        )

        result = module.handle_command("0")

        assert result["ok"] is True
        assert calls == [
            ("GET", "http://pi.example:5050/root/health", {"timeout": 10})
        ]
    finally:
        monkeypatch.setenv("TERMOCAM_PI_BASE_URL", "http://127.0.0.1:5000")
        importlib.reload(controller_commands)
