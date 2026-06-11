import os
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker


SERVER_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = SERVER_DIR / "data" / "termocam.db"
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DEFAULT_DB_PATH}")
if DATABASE_URL == f"sqlite:///{DEFAULT_DB_PATH}":
    DEFAULT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)


class Base(DeclarativeBase):
    pass


def _engine_kwargs(url: str) -> dict:
    if url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}}
    return {}


engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True, **_engine_kwargs(DATABASE_URL))
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _sqlite_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def init_db() -> None:
    from server import models  # noqa: F401

    Base.metadata.create_all(bind=engine)


async def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def database_ready() -> bool:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return True
