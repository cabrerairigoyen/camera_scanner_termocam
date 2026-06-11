from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), default="CAPTURING", index=True)
    course: Mapped[str | None] = mapped_column(String(120))
    language: Mapped[str | None] = mapped_column(String(16))
    title: Mapped[str | None] = mapped_column(String(255))
    idempotency_key: Mapped[str | None] = mapped_column(String(255), unique=True)
    active_job_id: Mapped[str | None] = mapped_column(String(40))
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    pages = relationship("DocumentPage", back_populates="document", cascade="all, delete-orphan")


class DocumentPage(Base):
    __tablename__ = "document_pages"
    __table_args__ = (UniqueConstraint("document_id", "page_number", name="uq_document_page_number"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    page_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(40), default="UPLOADED", index=True)
    source_artifact_id: Mapped[str | None] = mapped_column(String(40))
    ocr_artifact_id: Mapped[str | None] = mapped_column(String(40))
    quality_score: Mapped[float | None] = mapped_column(Float)
    accepted: Mapped[bool] = mapped_column(Boolean, default=False)
    rejection_reason: Mapped[str | None] = mapped_column(String(120))
    ocr_text: Mapped[str | None] = mapped_column(Text)
    metrics_json: Mapped[str | None] = mapped_column(Text)
    warnings_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    document = relationship("Document", back_populates="pages")


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_jobs_idempotency_key"),
        Index("ix_jobs_claim", "status", "priority", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"), index=True)
    page_id: Mapped[str | None] = mapped_column(ForeignKey("document_pages.id", ondelete="SET NULL"), index=True)
    type: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(32), default="QUEUED", index=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    current_step: Mapped[str | None] = mapped_column(String(80))
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    lease_owner: Mapped[str | None] = mapped_column(String(120))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_json: Mapped[str | None] = mapped_column(Text)
    result_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class JobStep(Base):
    __tablename__ = "job_steps"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(32))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    warnings_json: Mapped[str | None] = mapped_column(Text)
    error_json: Mapped[str | None] = mapped_column(Text)
    metrics_json: Mapped[str | None] = mapped_column(Text)


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"), index=True)
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"), index=True)
    page_id: Mapped[str | None] = mapped_column(ForeignKey("document_pages.id", ondelete="SET NULL"), index=True)
    kind: Mapped[str] = mapped_column(String(64), index=True)
    storage_key: Mapped[str] = mapped_column(String(255), unique=True)
    content_type: Mapped[str] = mapped_column(String(120))
    byte_size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("document_id", "sequence", name="uq_document_event_sequence"),
        UniqueConstraint("dedupe_key", name="uq_event_dedupe_key"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"), index=True)
    page_id: Mapped[str | None] = mapped_column(ForeignKey("document_pages.id", ondelete="SET NULL"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(16))
    message_key: Mapped[str] = mapped_column(String(80))
    spoken_text: Mapped[str | None] = mapped_column(Text)
    dedupe_key: Mapped[str | None] = mapped_column(String(255))
    payload_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Question(Base):
    __tablename__ = "questions"
    __table_args__ = (UniqueConstraint("document_id", "stable_question_id", name="uq_document_question"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    stable_question_id: Mapped[str] = mapped_column(String(120))
    type: Mapped[str] = mapped_column(String(32))
    text: Mapped[str] = mapped_column(Text)
    choices_json: Mapped[str | None] = mapped_column(Text)
    page_number: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Answer(Base):
    __tablename__ = "answers"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    question_id: Mapped[str] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"), index=True)
    solver_job_id: Mapped[str] = mapped_column(String(120), index=True)
    answer_text: Mapped[str | None] = mapped_column(Text)
    selected_choices_json: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    citations_json: Mapped[str | None] = mapped_column(Text)
    audio_artifact_id: Mapped[str | None] = mapped_column(String(40))
    model_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
