import os
import zipfile

import requests


def zip_session(session_path: str, zip_path: str) -> bool:
    try:
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for root, _dirs, files in os.walk(session_path):
                for filename in files:
                    path = os.path.join(root, filename)
                    archive.write(path, os.path.relpath(path, session_path))
        return True
    except Exception:
        return False


def upload_session(zip_path: str, server_url: str) -> dict:
    try:
        with open(zip_path, "rb") as handle:
            response = requests.post(
                server_url,
                files={"file": (os.path.basename(zip_path), handle, "application/zip")},
                timeout=120,
            )
        return {
            "status": "success" if response.ok else "error",
            "http_code": response.status_code,
            "response": response.json() if response.headers.get("content-type", "").startswith("application/json") else response.text,
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
