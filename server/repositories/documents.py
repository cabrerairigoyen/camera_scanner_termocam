from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.models import Document, DocumentPage, Event


def get_document(db: Session, document_id: str) -> Document | None:
    return db.get(Document, document_id)


def ordered_pages(db: Session, document_id: str) -> list[DocumentPage]:
    return list(
        db.scalars(
            select(DocumentPage)
            .where(DocumentPage.document_id == document_id)
            .order_by(DocumentPage.page_number.asc())
        )
    )


def next_page_number(db: Session, document_id: str) -> int:
    current = db.scalar(select(func.max(DocumentPage.page_number)).where(DocumentPage.document_id == document_id))
    return int(current or 0) + 1


def next_event_sequence(db: Session, document_id: str) -> int:
    current = db.scalar(select(func.max(Event.sequence)).where(Event.document_id == document_id))
    return int(current or 0) + 1
