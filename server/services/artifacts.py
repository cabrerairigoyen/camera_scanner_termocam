import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import BinaryIO

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.models import Artifact
from server.services.ids import new_id


ARTIFACT_ROOT = Path(
    os.getenv("ARTIFACT_ROOT", str(Path(__file__).resolve().parents[1] / "data" / "artifacts"))
).resolve()


class ArtifactStorageError(RuntimeError):
    pass


def _safe_path(storage_key: str) -> Path:
    if not storage_key or Path(storage_key).is_absolute():
        raise ArtifactStorageError("Invalid artifact storage key")
    path = (ARTIFACT_ROOT / storage_key).resolve()
    if ARTIFACT_ROOT != path and ARTIFACT_ROOT not in path.parents:
        raise ArtifactStorageError("Artifact path escapes storage root")
    return path


def artifact_path(artifact: Artifact) -> Path:
    return _safe_path(artifact.storage_key)


def save_bytes(
    db: Session,
    data: bytes,
    *,
    kind: str,
    content_type: str,
    job_id: str | None = None,
    document_id: str | None = None,
    page_id: str | None = None,
    extension: str = "",
) -> Artifact:
    artifact_id = new_id("art")
    suffix = extension if extension.startswith(".") or not extension else f".{extension}"
    storage_key = f"{artifact_id[:7]}/{artifact_id}{suffix}"
    destination = _safe_path(storage_key)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".artifact-", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, destination)
    except Exception as exc:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise ArtifactStorageError(str(exc)) from exc
    artifact = Artifact(
        id=artifact_id,
        job_id=job_id,
        document_id=document_id,
        page_id=page_id,
        kind=kind,
        storage_key=storage_key,
        content_type=content_type,
        byte_size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )
    db.add(artifact)
    db.flush()
    return artifact


def save_upload_file(db: Session, upload_file, **kwargs) -> Artifact:
    return save_bytes(db, upload_file.file.read(), **kwargs)


def save_json(db: Session, value, **kwargs) -> Artifact:
    return save_bytes(
        db,
        json.dumps(value, indent=2, ensure_ascii=True).encode("utf-8"),
        content_type="application/json",
        extension=".json",
        **kwargs,
    )


def open_artifact(artifact: Artifact) -> BinaryIO:
    return artifact_path(artifact).open("rb")


def get_artifact(db: Session, artifact_id: str) -> Artifact | None:
    return db.get(Artifact, artifact_id)


def latest_artifact(db: Session, job_id: str, kind: str) -> Artifact | None:
    return db.scalar(
        select(Artifact)
        .where(Artifact.job_id == job_id, Artifact.kind == kind)
        .order_by(Artifact.created_at.desc())
    )


def storage_ready() -> bool:
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".ready-", dir=ARTIFACT_ROOT)
    os.close(fd)
    os.unlink(name)
    return True
