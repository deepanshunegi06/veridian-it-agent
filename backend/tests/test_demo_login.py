"""Dev-only one-click demo login. No LLM, no Mongo, no network.

Covers POST /auth/demo-login: per-role sessions without a password, the
dev-mode gate, and that passwords/hashes never appear in responses.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.db as dbmod
import app.main as mainmod
from app.llm import get_settings

EMP_PW = "DemoLogin-Emp-01!"
AGENT_PW = "DemoLogin-Agent-01!"
ADMIN_PW = "DemoLogin-Admin-01!"


def _reset() -> None:
    dbmod._CLIENT = None
    dbmod._CLIENT_URI = None
    dbmod._MEMORY_CONVOS.clear()
    dbmod._MEMORY_EVENTS.clear()
    dbmod._MEMORY_USERS.clear()
    dbmod._MEMORY_COUNTERS["ticket_seq"] = 0
    mainmod.CONVERSATIONS._cache.clear()


def _seed_three() -> None:
    from app.auth import create_user_record

    create_user_record(
        email="employee@veridian.local",
        name="Eddie Employee",
        role="employee",
        password=EMP_PW,
    )
    create_user_record(
        email="agent@veridian.local", name="Ava Agent", role="it_agent", password=AGENT_PW
    )
    create_user_record(
        email="admin@veridian.local", name="Ada Admin", role="admin", password=ADMIN_PW
    )


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("MONGODB_URI", "")
    monkeypatch.setenv("VERIDIAN_DB", "veridian_it_agent_test")
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("JWT_SECRET", "test-secret-for-demo-login-only-0123456789")
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")
    get_settings.cache_clear()
    _reset()
    _seed_three()
    with TestClient(mainmod.app) as c:
        yield c
    _reset()


@pytest.mark.parametrize(
    "role,email",
    [
        ("employee", "employee@veridian.local"),
        ("it_agent", "agent@veridian.local"),
        ("admin", "admin@veridian.local"),
    ],
)
def test_demo_login_each_role(client: TestClient, role: str, email: str) -> None:
    r = client.post("/auth/demo-login", json={"role": role})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user"]["email"] == email
    assert body["user"]["role"] == role
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    # No password material ever leaves the server through this endpoint.
    assert "password" not in body
    assert "password" not in body["user"]
    assert "password_hash" not in body["user"]
    assert "veridian_access" in r.cookies
    # The minted token is a real session.
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200, me.text
    assert me.json()["email"] == email


def test_demo_login_rejects_unknown_role(client: TestClient) -> None:
    r = client.post("/auth/demo-login", json={"role": "superadmin"})
    assert r.status_code == 422, r.text


def test_demo_login_missing_user_is_404(client: TestClient) -> None:
    dbmod._MEMORY_USERS.clear()
    r = client.post("/auth/demo-login", json={"role": "admin"})
    assert r.status_code == 404, r.text


def test_demo_login_role_mismatch_is_404(client: TestClient) -> None:
    # The demo email exists but no longer holds the requested role.
    doc = dbmod.get_user_by_email("employee@veridian.local")
    assert doc is not None
    doc["role"] = "admin"
    dbmod.save_user(doc)
    assert client.post("/auth/demo-login", json={"role": "employee"}).status_code == 404


def test_demo_login_disabled_user_is_401(client: TestClient) -> None:
    doc = dbmod.get_user_by_email("admin@veridian.local")
    assert doc is not None
    doc["is_active"] = False
    dbmod.save_user(doc)
    r = client.post("/auth/demo-login", json={"role": "admin"})
    assert r.status_code == 401, r.text


def _prod_client(monkeypatch, **extra_env: str) -> TestClient:
    monkeypatch.setenv("MONGODB_URI", "")
    monkeypatch.setenv("VERIDIAN_DB", "veridian_it_agent_test")
    monkeypatch.setenv("JWT_SECRET", "test-secret-for-demo-login-only-0123456789")
    for k, v in extra_env.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    _reset()
    _seed_three()
    return TestClient(mainmod.app)


def test_demo_login_not_dev_is_404(monkeypatch) -> None:
    with _prod_client(monkeypatch, ENV="prod") as c:
        assert c.post("/auth/demo-login", json={"role": "admin"}).status_code == 404


def test_demo_login_seeding_disabled_is_404(monkeypatch) -> None:
    with _prod_client(monkeypatch, ENV="dev", SEED_DEMO_USERS="false") as c:
        assert c.post("/auth/demo-login", json={"role": "admin"}).status_code == 404
