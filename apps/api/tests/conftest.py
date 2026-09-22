"""Tes integrasi terhadap PostgreSQL sungguhan (RLS tidak bisa diuji dengan SQLite).
Butuh DATABASE_URL (role app) yang sudah dimigrasi."""
import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("LOGIN_RATE_LIMIT_PER_MINUTE", "1000")

from app.core import ratelimit  # noqa: E402
from app.main import app  # noqa: E402
from scripts.bootstrap import ensure_tenant_with_admin  # noqa: E402

SA_EMAIL = "root@nexvora.id"
PW = "Sup3r-Secret-Pass!"


@pytest.fixture(scope="session")
async def client():
    await ensure_tenant_with_admin("platform", "Nexvora Platform", SA_EMAIL, PW, "SUPER_ADMIN")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_rl():
    ratelimit.reset_memory()


async def login(client, tenant, email, password=PW):
    r = await client.post("/api/v1/auth/login", json={"tenant": tenant, "email": email, "password": password})
    return r


def auth(tok):
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture(scope="session")
async def sa_token(client):
    r = await login(client, "platform", SA_EMAIL)
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


async def make_tenant(client, sa_token):
    slug = "t-" + uuid.uuid4().hex[:8]
    email = f"admin@{slug}.nexvora.id"
    r = await client.post("/api/v1/tenants", headers=auth(sa_token), json={
        "slug": slug, "name": slug.upper(), "admin_email": email, "admin_full_name": "Admin", "admin_password": PW})
    assert r.status_code == 201, r.text
    tok = (await login(client, slug, email)).json()
    return slug, email, tok
