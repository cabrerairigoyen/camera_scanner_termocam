from typing import Any, Literal

from pydantic import BaseModel, Field


class DocumentCreate(BaseModel):
    course: str | None = None
    language: str | None = None
    title: str | None = None


class DocumentFinish(BaseModel):
    expected_page_count: int = Field(gt=0)
    solve: bool = False
    answer_mode: Literal["shorter", "standard", "detailed"] = "standard"
    allow_rejected: bool = False


class PageMetadata(BaseModel):
    page_number: int = Field(gt=0)
    capture_mode: Literal["still", "sweep"] = "still"
    replace_page_id: str | None = None


class PageUpdate(BaseModel):
    page_number: int | None = Field(default=None, gt=0)


class PageReorder(BaseModel):
    page_ids: list[str]


class JobCreate(BaseModel):
    type: str
    document_id: str | None = None
    page_id: str | None = None
    priority: int = 100
    max_attempts: int = Field(default=3, ge=1, le=20)
    payload: dict[str, Any] = Field(default_factory=dict)


class JobRetry(BaseModel):
    from_step: str | None = None
    reason: str | None = None


class JobCancel(BaseModel):
    reason: str | None = None


class ErrorDetail(BaseModel):
    code: str
    message: str
    recoverable: bool
    recommended_action: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    retry_after_seconds: int | None = None
    trace_id: str


class ErrorResponse(BaseModel):
    status: Literal["FAILED"] = "FAILED"
    error: ErrorDetail
