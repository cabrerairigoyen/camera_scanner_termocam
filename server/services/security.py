import os
import zipfile
from pathlib import PurePosixPath

from fastapi import Header

from server.errors import api_error


MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))
MAX_ZIP_MEMBERS = int(os.getenv("MAX_ZIP_MEMBERS", "500"))
MAX_ZIP_EXPANDED_BYTES = int(os.getenv("MAX_ZIP_EXPANDED_BYTES", str(250 * 1024 * 1024)))
MAX_ZIP_COMPRESSION_RATIO = float(os.getenv("MAX_ZIP_COMPRESSION_RATIO", "100"))


async def require_service_token(authorization: str | None = Header(default=None)) -> None:
    expected = os.getenv("TERMOCAM_SERVICE_TOKEN")
    if not expected:
        return
    if authorization != f"Bearer {expected}":
        raise api_error(401, "INVALID_UPLOAD", "Invalid service credentials.")


async def require_solver_token(authorization: str | None = Header(default=None)) -> None:
    expected = os.getenv("SOLVER_SERVICE_TOKEN")
    if not expected:
        return
    if authorization != f"Bearer {expected}":
        raise api_error(401, "SOLVER_UNAVAILABLE", "Invalid solver credentials.")


def validate_upload(data: bytes, filename: str, allowed: set[str]) -> str:
    if not data:
        raise api_error(400, "INVALID_UPLOAD", "Upload is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise api_error(413, "INVALID_UPLOAD", "Upload exceeds maximum size.")
    lower = (filename or "").lower()
    if "image" in allowed and (
        data.startswith(b"\xff\xd8\xff")
        or data.startswith(b"\x89PNG\r\n\x1a\n")
    ):
        return "image/png" if data.startswith(b"\x89PNG") else "image/jpeg"
    if "zip" in allowed and data.startswith(b"PK\x03\x04"):
        validate_zip_bytes(data)
        return "application/zip"
    if "pdf" in allowed and data.startswith(b"%PDF-"):
        return "application/pdf"
    raise api_error(400, "INVALID_UPLOAD", f"Unsupported or malformed upload: {lower}")


def validate_zip_bytes(data: bytes) -> None:
    import io

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            if len(members) > MAX_ZIP_MEMBERS:
                raise api_error(400, "ZIP_UNSAFE", "ZIP contains too many members.")
            expanded = 0
            for member in members:
                path = PurePosixPath(member.filename)
                if path.is_absolute() or ".." in path.parts:
                    raise api_error(400, "ZIP_UNSAFE", "ZIP contains an unsafe path.")
                mode = member.external_attr >> 16
                if (mode & 0o170000) == 0o120000:
                    raise api_error(400, "ZIP_UNSAFE", "ZIP symlinks are not allowed.")
                expanded += member.file_size
                if expanded > MAX_ZIP_EXPANDED_BYTES:
                    raise api_error(400, "ZIP_UNSAFE", "ZIP expands beyond the allowed size.")
                if member.compress_size == 0:
                    ratio = float("inf") if member.file_size else 1.0
                else:
                    ratio = member.file_size / member.compress_size
                if ratio > MAX_ZIP_COMPRESSION_RATIO:
                    raise api_error(400, "ZIP_UNSAFE", "ZIP compression ratio is unsafe.")
    except zipfile.BadZipFile as exc:
        raise api_error(400, "INVALID_UPLOAD", "Invalid ZIP archive.") from exc
