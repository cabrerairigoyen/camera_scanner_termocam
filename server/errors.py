import uuid

from fastapi import HTTPException


def error_payload(
    code: str,
    message: str,
    *,
    recoverable: bool = False,
    recommended_action: str | None = None,
    details: dict | None = None,
    retry_after_seconds: int | None = None,
    trace_id: str | None = None,
) -> dict:
    return {
        "status": "FAILED",
        "error": {
            "code": code,
            "message": message,
            "recoverable": recoverable,
            "recommended_action": recommended_action,
            "details": details or {},
            "retry_after_seconds": retry_after_seconds,
            "trace_id": trace_id or f"trace_{uuid.uuid4().hex}",
        },
    }


def api_error(status_code: int, code: str, message: str, **kwargs) -> HTTPException:
    return HTTPException(status_code=status_code, detail=error_payload(code, message, **kwargs))
