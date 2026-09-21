"""Regression: automatic employee ticket lifecycle.

Every employee thread (POST /threads) gets exactly one automatic ticket,
independent of the AI run. The AI may ask follow-ups or resolve per policy,
but creation itself never escalates. A later AI ``raise_ticket`` reuses the
same id instead of opening a second ticket.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.db as dbmod
import app.main as mainmod
from app import agent as agent_mod
from app import tools as tools_mod
from app.auth import create_user_record
from app.llm import get_settings
from app.tools import Conversation

EMP_PW = "Employee-Pass-01!"
EMP2_PW = "Employee2-Pass!"
AGENT_PW = "Agent-Pass-01!!"
ADMIN_PW = "Admin-Pass-01!!"


class _FakeResponse:
    def __init__(self, tool_calls, content=""):
        self.tool_calls = tool_calls
        self.content = content


class _FakeModel:
    def __init__(self, script):
        self.script = [list(batch) for batch in script]
        self.invokes = 0

    def bind_tools(self, _schemas):
        return self

    def invoke(self, _messages):
        if self.invokes < len(self.script):
            batch = self.script[self.invokes]
        else:
            batch = [
                {
                    "name": "escalate",
                    "args": {"reason": "should not happen", "to": "IT lead", "cites": []},
                    "id": f"extra-{self.invokes}",
                }
            ]
        self.invokes += 1
        return _FakeResponse(batch)


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("MONGODB_URI", "")
    monkeypatch.setenv("VERIDIAN_DB", "veridian_it_agent_test")
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("JWT_SECRET", "test-secret-for-ticket-lifecycle-0123456789")
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
    create_user_record(
        email="agent@veridian.local", name="Ava Agent", role="it_agent", password=AGENT_PW
    )
    create_user_record(
        email="admin@veridian.local", name="Ada Admin", role="admin", password=ADMIN_PW
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


def _drain(raw: TestClient, method: str, path: str, token: str, body: dict):
    if method == "POST":
        r = raw.post(path, json=body, headers=_authz(token))
    else:
        raise AssertionError("only POST used here")
    assert r.status_code == 200, r.text
    _ = r.text
    return r


def test_first_thread_gets_exactly_one_ticket(client):
    tok = _login(client, "employee@veridian.local", EMP_PW)
    created = client.post("/threads", json={"message": "my laptop is dead"}, headers=_authz(tok))
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["ticket_id"], body
    assert body["ticket"] == body["ticket_id"]
    assert body["ticket_status"] in ("open", "in_progress")

    rid = body["request_id"]
    detail = client.get(f"/threads/{rid}", headers=_authz(tok)).json()
    assert detail["ticket_id"] == body["ticket_id"]
    assert detail["ticket"] == body["ticket_id"]
    assert detail["outcome"] is None
    assert detail["outcome"] != "escalated"
    assert detail["ticket_status"] in ("open", "in_progress")

    summaries = client.get("/threads", headers=_authz(tok)).json()
    mine = next(t for t in summaries if t["request_id"] == rid)
    assert mine["ticket_id"] == body["ticket_id"]
    assert mine["ticket"] == body["ticket_id"]

    mine_tickets = client.get("/tickets", headers=_authz(tok)).json()
    auto = [t for t in mine_tickets if t.get("request_id") == rid]
    assert len(auto) == 1, auto
    assert auto[0]["id"] == body["ticket_id"]


def test_ticket_remains_open_while_agent_asks_followup_no_escalation(client, monkeypatch):
    script = [
        [{"name": "find_policy", "args": {"query": "laptop replacement age"}, "id": "c1"}],
        [
            {
                "name": "ask_followup",
                "args": {"question": "How old is the laptop?", "why": "decides rule"},
                "id": "c2",
            }
        ],
    ]
    monkeypatch.setattr(agent_mod, "build_llm", lambda *a, **k: _FakeModel(script))
    tok = _login(client, "employee@veridian.local", EMP_PW)
    rid = client.post("/threads", json={"message": "my laptop is dead"}, headers=_authz(tok)).json()[
        "request_id"
    ]
    auto_id = client.get(f"/threads/{rid}", headers=_authz(tok)).json()["ticket_id"]
    assert auto_id

    with TestClient(mainmod.app) as raw:
        r = raw.post(
            f"/threads/{rid}/messages", json={"message": "my laptop is dead"}, headers=_authz(tok)
        )
        assert r.status_code == 200, r.text
        _ = r.text

    detail = client.get(f"/threads/{rid}", headers=_authz(tok)).json()
    assert detail["ticket_id"] == auto_id
    assert detail["ticket_status"] == "open"
    assert detail["outcome"] == "waiting_on_employee"
    assert detail["outcome"] != "escalated"
    assert not any(a["tool"] == "escalate" for a in detail["actions"])
    users = [m for m in detail["messages"] if m["speaker"] == "user"]
    agents = [m for m in detail["messages"] if m["speaker"] == "agent"]
    assert len(users) == 1
    assert len(agents) == 1

    tickets = client.get("/tickets", headers=_authz(tok)).json()
    rows = [t for t in tickets if t.get("request_id") == rid]
    assert len(rows) == 1
    assert rows[0]["id"] == auto_id
    assert rows[0]["ticket_status"] == "open"
    assert rows[0]["outcome"] == "waiting_on_employee"


def test_ticket_appears_after_cache_reload(client):
    tok = _login(client, "employee@veridian.local", EMP_PW)
    created = client.post(
        "/threads", json={"message": "vpn keeps dropping"}, headers=_authz(tok)
    ).json()
    rid = created["request_id"]
    ticket_id = created["ticket_id"]
    assert ticket_id

    mainmod.CONVERSATIONS._cache.clear()
    detail = client.get(f"/threads/{rid}", headers=_authz(tok)).json()
    assert detail["ticket_id"] == ticket_id
    assert detail["ticket_status"] in ("open", "in_progress")

    stored = dbmod.load_conversation(rid)
    assert stored is not None
    assert stored.get("ticket_id") == ticket_id

    tickets = client.get("/tickets", headers=_authz(tok)).json()
    assert ticket_id in {t["id"] for t in tickets if t.get("request_id") == rid}


def test_ai_raise_ticket_reuses_existing_no_duplicate(client):
    tok = _login(client, "employee@veridian.local", EMP_PW)
    first = client.post(
        "/threads", json={"message": "my laptop screen flickers"}, headers=_authz(tok)
    ).json()
    second = client.post(
        "/threads", json={"message": "mouse battery dead"}, headers=_authz(tok)
    ).json()
    assert first["ticket_id"] != second["ticket_id"]

    convo = mainmod.CONVERSATIONS.get(first["request_id"])
    assert convo is not None
    before_seq = int(dbmod._MEMORY_COUNTERS.get("ticket_seq", 0))
    out = tools_mod.raise_ticket(convo, "hardware", "Fix laptop screen", "normal")
    assert out["ticket"] == first["ticket_id"]
    after_seq = int(dbmod._MEMORY_COUNTERS.get("ticket_seq", 0))
    assert after_seq == before_seq, "reuse must not consume the global counter"
    assert convo.outcome == "ticket_raised"
    assert convo.ticket_status == "open"
    mainmod.CONVERSATIONS._cache[first["request_id"]] = convo

    tickets = client.get("/tickets", headers=_authz(tok)).json()
    rows = [t for t in tickets if t.get("request_id") == first["request_id"]]
    assert len(rows) == 1, rows
    assert rows[0]["id"] == first["ticket_id"]
    assert rows[0]["category"] == "hardware"

    # Refusals keep their semantics even with an auto ticket present.
    bad = tools_mod.raise_ticket(convo, "hardware", "x", "bogus")
    assert bad["refused"] == "bad_priority"
    assert convo.ticket_id == first["ticket_id"]

    c2 = Conversation(request_id="T-DIRECT", employee="Aditi", text="guest wifi for a visitor")
    assert c2.ticket_id is None
    tools_mod.find_policy(c2, "guest wifi visitor")
    refused = tools_mod.raise_ticket(c2, "network", "Guest wifi", "normal")
    assert refused["refused"] == "no_ticket_needed"

    # Direct tool convo without an auto ticket keeps the legacy allocate path.
    c3 = Conversation(request_id="T-DIRECT2", employee="Aditi", text="my laptop is dead")
    assert c3.ticket_id is None
    created_out = tools_mod.raise_ticket(c3, "hardware", "Replace laptop", "high")
    assert created_out["ticket"]
    assert c3.ticket_id == created_out["ticket"]
    assert c3.outcome == "ticket_raised"


def test_visibility_and_join_resolve_consistency(client):
    t1 = _login(client, "employee@veridian.local", EMP_PW)
    t2 = _login(client, "employee2@veridian.local", EMP2_PW)
    atok = _login(client, "agent@veridian.local", AGENT_PW)
    adm = _login(client, "admin@veridian.local", ADMIN_PW)

    r1 = client.post("/threads", json={"message": "printer jammed badly"}, headers=_authz(t1)).json()
    r2 = client.post("/threads", json={"message": "vpn broken"}, headers=_authz(t2)).json()
    assert r1["ticket_id"] != r2["ticket_id"]

    # Employees see only their own ticket; support/admin see all.
    mine1 = {t["id"] for t in client.get("/tickets", headers=_authz(t1)).json()}
    mine2 = {t["id"] for t in client.get("/tickets", headers=_authz(t2)).json()}
    assert r1["ticket_id"] in mine1
    assert r2["ticket_id"] not in mine1
    assert r2["ticket_id"] in mine2
    assert r1["ticket_id"] not in mine2
    support_ids = {t["id"] for t in client.get("/tickets", headers=_authz(atok)).json()}
    assert r1["ticket_id"] in support_ids and r2["ticket_id"] in support_ids
    admin_ids = {t["id"] for t in client.get("/tickets", headers=_authz(adm)).json()}
    assert r1["ticket_id"] in admin_ids and r2["ticket_id"] in admin_ids

    # Cross-employee thread reads stay forbidden.
    assert client.get(f"/threads/{r1['request_id']}", headers=_authz(t2)).status_code == 403

    # Join flips open -> assigned without changing the ticket id; resolve closes it.
    joined = client.post(f"/threads/{r1['request_id']}/join", json={}, headers=_authz(atok))
    assert joined.status_code == 200, joined.text
    assert joined.json()["ticket_status"] == "assigned"
    assert joined.json()["ticket_id"] == r1["ticket_id"]

    reply = client.post(
        f"/threads/{r1['request_id']}/human", json={"message": "On my way."}, headers=_authz(atok)
    )
    assert reply.status_code == 200, reply.text

    resolved = client.post(f"/threads/{r1['request_id']}/resolve", json={}, headers=_authz(atok))
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["ticket_status"] == "resolved"
    assert resolved.json()["ticket_id"] == r1["ticket_id"]

    detail = client.get(f"/threads/{r1['request_id']}", headers=_authz(atok)).json()
    assert detail["ticket_id"] == r1["ticket_id"]
    tickets = client.get("/tickets", headers=_authz(atok)).json()
    row = next(t for t in tickets if t.get("request_id") == r1["request_id"])
    assert row["id"] == r1["ticket_id"]
    assert row["ticket_status"] == "resolved"
