from fastapi import APIRouter

from server.db import database_ready
from server.services.artifacts import storage_ready


router = APIRouter(tags=["health"])


@router.get("/health/live")
async def live():
    return {"status": "ok"}


@router.get("/health/ready")
async def ready():
    checks = {"database": False, "artifact_storage": False}
    try:
        checks["database"] = database_ready()
        checks["artifact_storage"] = storage_ready()
    except Exception:
        return {"status": "not_ready", "checks": checks}
    return {"status": "ready", "checks": checks}


@router.get("/health")
async def legacy_health():
    return await ready()
