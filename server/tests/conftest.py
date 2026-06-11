import io
import asyncio

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from server.app import app
from server.db import Base, get_db
from server.services import artifacts


class ASGIClient:
    def __init__(self, application):
        self.application = application

    def request(self, method, path, **kwargs):
        async def run():
            transport = httpx.ASGITransport(app=self.application)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.request(method, path, **kwargs)

        return asyncio.run(run())

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path, **kwargs):
        return self.request("POST", path, **kwargs)

    def put(self, path, **kwargs):
        return self.request("PUT", path, **kwargs)

    def delete(self, path, **kwargs):
        return self.request("DELETE", path, **kwargs)


@pytest.fixture
def session_factory(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(artifacts, "ARTIFACT_ROOT", (tmp_path / "artifacts").resolve())
    yield factory
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def db(session_factory):
    with session_factory() as session:
        yield session
        session.rollback()


@pytest.fixture
def client(session_factory):
    async def override_db():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    test_client = ASGIClient(app)
    yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def png_bytes():
    from PIL import Image

    output = io.BytesIO()
    Image.new("RGB", (32, 32), "white").save(output, "PNG")
    return output.getvalue()
