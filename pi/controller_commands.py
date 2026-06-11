import logging
import os
from typing import Any

import requests


LOGGER = logging.getLogger(__name__)
PI_BASE_URL = os.getenv("TERMOCAM_PI_BASE_URL", "http://127.0.0.1:5000").rstrip("/")

COMMAND_MAP = {
    "A": ("POST", "/document/capture-still"),
    "B": ("POST", "/document/capture-sweep/start"),
    "C": ("POST", "/document/capture-sweep/stop"),
    "D": ("POST", "/document/finish"),
    "0": ("GET", "/health"),
    "#": ("GET", "/document/events"),
    "*": ("GET", "/document/status"),
}


def handle_command(command: str) -> dict[str, Any]:
    command = command.strip().upper() if isinstance(command, str) else ""
    if command not in COMMAND_MAP:
        return {
            "ok": False,
            "command": command,
            "method": None,
            "path": None,
            "error": {"code": "UNKNOWN_COMMAND", "message": "Unsupported command."},
        }
    method, path = COMMAND_MAP[command]
    url = f"{PI_BASE_URL}{path}"
    result = {"command": command, "method": method, "path": path}
    try:
        response = requests.request(method, url, timeout=10)
        if response.headers.get("content-type", "").startswith("application/json"):
            try:
                body: Any = response.json()
            except ValueError:
                return {
                    "ok": False,
                    **result,
                    "status_code": response.status_code,
                    "error": {
                        "code": "INVALID_RESPONSE",
                        "message": "Pi server returned invalid JSON.",
                    },
                }
        else:
            body = response.text
        return {
            "ok": response.ok,
            **result,
            "status_code": response.status_code,
            "response": body,
        }
    except requests.Timeout:
        LOGGER.warning("Command timed out: %s", command)
        return {
            "ok": False,
            **result,
            "error": {"code": "TIMEOUT", "message": "Command timed out."},
        }
    except requests.ConnectionError:
        LOGGER.warning("Command connection error: %s", command)
        return {
            "ok": False,
            **result,
            "error": {"code": "SERVER_OFFLINE", "message": "Pi server is offline."},
        }
    except requests.RequestException as exc:
        LOGGER.warning("Command request failed: %s", command)
        return {
            "ok": False,
            **result,
            "error": {"code": "NETWORK_ERROR", "message": str(exc)},
        }
