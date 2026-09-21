"""Regression: lazy hydration for pre-ticket LIVE threads.

Old employee threads created before automatic ticket_id support can persist
without ticket_id. A safe read (GET /threads/{id}, list, tickets) must attach
exactly one automatic ticket, persist it, and never alter outcome/escalation.
Legacy REQ-* fixture rows must never be auto-ticketed en masse.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.db as dbmod
import app.main as mainmod
from app.auth import create_user_record
from app.llm import get_settings
from app.tools import Conversation

EMP_PW = "Employee-Pass-01!"
EMP2_PW = "Employee2-Pass!"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("MONGODB_URI", "")
    monkeypatch.setenv("VERIDIAN_DB", "veridian_it_agent_test_hydration")
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("JWT_SECRET", "test-secret-for-ticket-hydration-0123456789")
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")
    get_settings.cache_clear()
    dbmod._CLIENT = None
    dbmod._CLIENT_URI = None
    dbmod._MEMORY_CONVOS.clear()
    dbmod._MEMORY_EVENTS.clear()
    dbmod._MEMORY_USERS.clear()
    dbmod._MEMORY_COUNTERS["ticket_seq"] = 0
    mainmod.CONVERSATIONS._cache.clear()
    create_user_record(
        email="employee@veridian.local", name="Eddie Employee", role="employee", password=EMP_PW
    )
    create_user_record(
        email="employee2@veridian.local", name="Erin Employee", role="employee", password=EMP2_PW
    )
    with TestClient(mainmod.app) as c:
        yield c
    mainmod.CONVERSATIONS._cache.clear()
    dbmod._MEMORY_CONVOS.clear()
    dbmod._MEMORY_USERS.clear()


def _login(c: TestClient, email: str, password: str) -> str:
    r = c.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _authz(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _seed_old_live_no_ticket(owner_user_id: str, rid: str = "LIVE-OLD0001") -> str:
    convo = Conversation(
        request_id=rid,
        employee="Eddie Employee",
        text="my old laptop issue from before tickets",
        owner_user_id=owner_user_id,
    )
    assert convo.ticket_id is None
    dbmod.save_conversation(convo)
    mainmod.CONVERSATIONS._cache.clear()
    stored = dbmod.load_conversation(rid)
    assert stored is not None
    assert not stored.get("ticket_id")
    return rid


def test_old_owner_live_thread_hydrates_once_and_persists(client):
    tok = _login(client, "employee@veridian.local", EMP_PW)
    owner = dbmod.get_user_by_email("employee@veridian.local")
    assert owner is not None
    rid = _seed_old_live_no_ticket(str(owner["id"]))

    before = int(dbmod._MEMORY_COUNTERS.get("ticket_seq", 0))
    detail = client.get(f"/threads/{rid}", headers=_authz(tok))
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["ticket_id"], body
    assert body["ticket"] == body["ticket_id"]
    assert body["ticket_status"] in ("open", "assigned")
    assert body["outcome"] is None
    after = int(dbmod._MEMORY_COUNTERS.get("ticket_seq", 0))
    assert after == before + 1

    stored = dbmod.load_conversation(rid)
    assert stored is not None
    assert stored.get("ticket_id") == body["ticket_id"]

    # Second read reuses the same ticket without consuming the counter.
    again = client.get(f"/threads/{rid}", headers=_authz(tok)).json()
    assert again["ticket_id"] == body["ticket_id"]
    assert int(dbmod._MEMORY_COUNTERS.get("ticket_seq", 0)) == after

    summaries = client.get("/threads", headers=_authz(tok)).json()
    mine = next(t for t in summaries if t["request_id"] == rid)
    assert mine["ticket_id"] == body["ticket_id"]
    assert mine["ticket"] == body["ticket_id"]

    tickets = client.get("/tickets", headers=_authz(tok)).json()
    rows = [t for t in tickets if t.get("request_id") == rid]
    assert len(rows) == 1, rows
    assert rows[0]["id"] == body["ticket_id"]
    assert rows[0]["ticket_id"] == body["ticket_id"]


def test_hydration_preserves_outcome_and_escalation(client):
    tok = _login(client, "employee@veridian.local", EMP_PW)
    owner = dbmod.get_user_by_email("employee@veridian.local")
    assert owner is not None
    rid = "LIVE-OLD0002"
    convo = Conversation(
        request_id=rid,
        employee="Eddie Employee",
        text="conflicting laptop policy question",
        owner_user_id=str(owner["id"]),
    )
    convo.outcome = "escalated"
    convo.ticket_status = "open"
    assert convo.ticket_id is None
    dbmod.save_conversation(convo)
    mainmod.CONVERSATIONS._cache.clear()

    body = client.get(f"/threads/{rid}", headers=_authz(tok)).json()
    assert body["ticket_id"]
    assert body["outcome"] == "escalated"
    assert body["ticket_status"] == "open"
    assert body["outcome"] != "resolved"


def test_legacy_req_rows_are_not_auto_ticketed(client):
    tok = _login(client, "employee@veridian.local", EMP_PW)
    owner = dbmod.get_user_by_email("employee@veridian.local")
    assert owner is not None
    rid = "REQ-OLD99"
    convo = Conversation(
        request_id=rid,
        employee="Eddie Employee",
        text="legacy fixture row",
        owner_user_id=str(owner["id"]),
    )
    assert convo.ticket_id is None
    dbmod.save_conversation(convo)
    mainmod.CONVERSATIONS._cache.clear()
    before = int(dbmod._MEMORY_COUNTERS.get("ticket_seq", 0))

    body = client.get(f"/threads/{rid}", headers=_authz(tok))
    assert body.status_code == 200, body.text
    payload = body.json()
    assert payload["ticket_id"] is None
    assert payload["ticket"] is None
    assert int(dbmod._MEMORY_COUNTERS.get("ticket_seq", 0)) == before

    stored = dbmod.load_conversation(rid)
    assert stored is not None
    assert not stored.get("ticket_id")


def test_other_employee_cannot_read_and_does_not_hydrate(client):
    t1 = _login(client, "employee@veridian.local", EMP_PW)
    t2 = _login(client, "employee2@veridian.local", EMP2_PW)
    owner = dbmod.get_user_by_email("employee@veridian.local")
    assert owner is not None
    rid = _seed_old_live_no_ticket(str(owner["id"]), rid="LIVE-OLD0003")

    forbidden = client.get(f"/threads/{rid}", headers=_authz(t2))
    assert forbidden.status_code == 403, forbidden.text
    # Forbidden read must not hydrate; the owner still triggers exactly one.
    assert not (dbmod.load_conversation(rid) or {}).get("ticket_id")
    owned = client.get(f"/threads/{rid}", headers=_authz(t1)).json()
    assert owned["ticket_id"]
