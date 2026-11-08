import os
import subprocess
import uuid

import pytest

PG = os.environ.get("SHC_TEST_PG", "postgresql://postgres@127.0.0.1:55432")
DB_NAME = f"shc_t_{uuid.uuid4().hex[:8]}"
os.environ["SHC_DATABASE_URL"] = PG.replace("postgresql://", "postgresql+asyncpg://") + f"/{DB_NAME}"
os.environ["SHC_SECRET_KEY"] = "test-secret-key-0123456789-abcdefghijklmnop"
os.environ["SHC_COOKIE_SECURE"] = "false"


def _psql(sql: str) -> None:
    subprocess.run(["psql", PG + "/postgres", "-q", "-c", sql], check=True)


@pytest.fixture(scope="session", autouse=True)
def database(tmp_path_factory):
    os.environ["SHC_BACKUP_DIR"] = str(tmp_path_factory.mktemp("backups"))
    os.environ["SHC_DATA_DIR"] = str(tmp_path_factory.mktemp("data"))
    from shc_api.config import get_settings

    get_settings.cache_clear()
    _psql(f"CREATE DATABASE {DB_NAME}")
    from shc_api.cli import migrate

    migrate()
    yield
    _psql(f"DROP DATABASE IF EXISTS {DB_NAME} WITH (FORCE)")


@pytest.fixture(scope="session")
async def app():
    from shc_api.main import create_app

    return create_app()


@pytest.fixture
async def client(app):
    import httpx

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


class Session:
    def __init__(self, client, csrf):
        self.c = client
        self.csrf = csrf

    def h(self):
        return {"X-CSRF-Token": self.csrf}

    async def get(self, url, **kw):
        return await self.c.get("/api/v1" + url, **kw)

    async def post(self, url, json=None, **kw):
        return await self.c.post("/api/v1" + url, json=json, headers=self.h(), **kw)

    async def put(self, url, json=None):
        return await self.c.put("/api/v1" + url, json=json, headers=self.h())

    async def delete(self, url):
        return await self.c.delete("/api/v1" + url, headers=self.h())


ADMIN_PW = "correct horse battery staple"


@pytest.fixture
async def admin(client):
    r = await client.get("/api/v1/auth/status")
    if r.json()["setup_required"]:
        r = await client.post("/api/v1/auth/setup", json={"username": "beheer", "password": ADMIN_PW})
    else:
        r = await client.post("/api/v1/auth/login", json={"username": "beheer", "password": ADMIN_PW})
    assert r.status_code == 200, r.text
    return Session(client, r.json()["csrf_token"])


@pytest.fixture
async def home(admin):
    r = await admin.get("/homes")
    if r.json():
        return r.json()[0]
    r = await admin.post("/homes", json={"name": "Thuis"})
    assert r.status_code == 200, r.text
    return r.json()


def pytest_collection_modifyitems(items):
    # One event loop for the whole session: the SQLAlchemy pool is bound to it.
    import inspect

    for item in items:
        if inspect.iscoroutinefunction(getattr(item, "function", None)):
            item.add_marker(pytest.mark.asyncio(loop_scope="session"), append=False)
