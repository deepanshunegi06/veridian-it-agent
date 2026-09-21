"""Regression: resolved threads reopen on a new employee message.

- AI-resolved (tools.resolve, resolution_source=ai) reopens to the AI via the
  normal stream (model runs, say present, ticket preserved).
- Human-resolved (/threads/{id}/resolve, resolution_source=human) reopens
  into a quiet IT handoff (no model, no say, outcome=escalated, assignment
  cleared) until a human joins again.
- Legacy docs missing resolution_source reopen with AI behaviour.
- Blank/ownership still enforced on resolved threads; ticket ids/titles kept.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import app.db as dbmod
import app.main as mainmod
from app import agent as agent_mod
from app import tools as tools_mod
from app.auth import create_user_record
from app.llm import get_settings

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
                    "args": {"reason": "stuck", "to": "IT lead", "cites": []},
                    "id": f"extra-{self.invokes}",
                }
            ]
        self.invokes += 1
        return _FakeResponse(batch)


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("MONGODB_URI", "")
    monkeypatch.setenv("VERIDIAN_DB", "veridian_it_agent_test_reopen")
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("JWT_SECRET", "test-secret-for-resolved-reopen-0123456789")
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


def _sse_events(text: str) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for block in text.split("\n\n"):
        name: str | None = None
        payload: dict = {}
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line.split("event:", 1)[1].strip()
            elif line.startswith("data:"):
                try:
                    payload = json.loads(line.split("data:", 1)[1].strip())
                except Exception:
                    payload = {}
        if name:
            out.append((name, payload))
    return out


def test_ai_resolved_reopen_runs_ai_again(client, monkeypatch):
    monkeypatch.setattr(agent_mod, "generate_ticket_title", lambda text: "Laptop issue")
    tok = _login(client, "employee@veridian.local", EMP_PW)
    created = client.post("/threads", json={"message": "my laptop is dead"}, headers=_authz(tok)).json()
    rid = created["request_id"]
    ticket_id = created["ticket_id"]
    assert ticket_id

    convo = mainmod.CONVERSATIONS.get(rid)
    assert convo is not None
    tools_mod.find_policy(convo, "guest wifi visitor")
    out = tools_mod.resolve(convo, "Generate one at the front-desk kiosk.", ["KB-07"])
    assert out.get("resolved") is True
    assert convo.resolution_source == "ai"
    mainmod.CONVERSATIONS._cache[rid] = convo
    assert client.get(f"/threads/{rid}", headers=_authz(tok)).json()["resolution_source"] == "ai"

    reopen_script = [
        [{"name": "find_policy", "args": {"query": "laptop followup"}, "id": "c1"}],
        [
            {
                "name": "ask_followup",
                "args": {"question": "What changed?", "why": "new info"},
                "id": "c2",
            }
        ],
    ]
    fake = _FakeModel(reopen_script)
    monkeypatch.setattr(agent_mod, "build_llm", lambda *a, **k: fake)
    with TestClient(mainmod.app) as raw:
        r = raw.post(
            f"/threads/{rid}/messages", json={"message": "actually it is still broken"}, headers=_authz(tok)
        )
        assert r.status_code == 200, r.text
        events = _sse_events(r.text)
    assert fake.invokes == 2
    assert "say" in [name for name, _ in events]
    detail = client.get(f"/threads/{rid}", headers=_authz(tok)).json()
    assert detail["ticket_id"] == ticket_id
    assert detail["ticket"] == ticket_id
    assert detail["outcome"] == "waiting_on_employee"
    assert detail["resolution_source"] is None
    users = [m for m in detail["messages"] if m["speaker"] == "user"]
    assert users and users[-1]["text"] == "actually it is still broken"
    stored = dbmod.load_conversation(rid)
    assert stored is not None
    assert stored.get("resolution_source") is None
    assert stored.get("ticket_id") == ticket_id


def test_human_resolved_reopen_goes_quiet_handoff_no_model(client, monkeypatch):
    monkeypatch.setattr(agent_mod, "generate_ticket_title", lambda text: "Printer issue")
    tok = _login(client, "employee@veridian.local", EMP_PW)
    atok = _login(client, "agent@veridian.local", AGENT_PW)
    rid = client.post("/threads", json={"message": "printer jammed badly"}, headers=_authz(tok)).json()[
        "request_id"
    ]
    ticket_id = client.get(f"/threads/{rid}", headers=_authz(tok)).json()["ticket_id"]
    assert client.post(f"/threads/{rid}/join", json={}, headers=_authz(atok)).status_code == 200
    resolved = client.post(f"/threads/{rid}/resolve", json={}, headers=_authz(atok))
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["resolution_source"] == "human"
    assert resolved.json()["ticket_status"] == "resolved"

    agent_msgs_before = len(
        [
            m
            for m in client.get(f"/threads/{rid}", headers=_authz(tok)).json()["messages"]
            if m["speaker"] == "agent"
        ]
    )

    def _boom(*a, **k):
        raise AssertionError("model must not run after human-resolved reopen")

    monkeypatch.setattr(agent_mod, "build_llm", _boom)
    with TestClient(mainmod.app) as raw:
        r = raw.post(
            f"/threads/{rid}/messages", json={"message": "it jammed again"}, headers=_authz(tok)
        )
        assert r.status_code == 200, r.text
        events = _sse_events(r.text)
    kinds = [name for name, _ in events]
    assert "say" not in kinds
    assert "handoff" in kinds
    assert "start" in kinds and "done" in kinds
    detail = client.get(f"/threads/{rid}", headers=_authz(tok)).json()
    assert detail["outcome"] == "escalated"
    assert detail["ticket_status"] == "open"
    assert detail["ticket_id"] == ticket_id
    assert detail["assigned_to"] is None
    assert detail["resolution_source"] is None
    assert [m for m in detail["messages"] if m["speaker"] == "user"][-1]["text"] == "it jammed again"
    assert len([m for m in detail["messages"] if m["speaker"] == "agent"]) == agent_msgs_before

    # A human can join again and reply after the handoff reopen.
    joined = client.post(f"/threads/{rid}/join", json={}, headers=_authz(atok))
    assert joined.status_code == 200, joined.text
    assert joined.json()["assigned_to"] == "Ava Agent"
    reply = client.post(
        f"/threads/{rid}/human", json={"message": "On my way again."}, headers=_authz(atok)
    )
    assert reply.status_code == 200, reply.text
    assert reply.json()["message"]["speaker"] == "human"


def test_legacy_resolved_without_source_reopens_with_ai(client, monkeypatch):
    monkeypatch.setattr(agent_mod, "generate_ticket_title", lambda text: "Legacy issue")
    tok = _login(client, "employee@veridian.local", EMP_PW)
    rid = client.post("/threads", json={"message": "legacy resolved thread"}, headers=_authz(tok)).json()[
        "request_id"
    ]
    convo = mainmod.CONVERSATIONS.get(rid)
    assert convo is not None
    convo.outcome = "resolved"
    convo.ticket_status = "resolved"
    if hasattr(convo, "resolution_source"):
        convo.resolution_source = None
    mainmod.CONVERSATIONS._cache[rid] = convo

    reopen_script = [
        [{"name": "find_policy", "args": {"query": "legacy"}, "id": "c1"}],
        [
            {
                "name": "ask_followup",
                "args": {"question": "What changed?", "why": "new info"},
                "id": "c2",
            }
        ],
    ]
    fake = _FakeModel(reopen_script)
    monkeypatch.setattr(agent_mod, "build_llm", lambda *a, **k: fake)
    with TestClient(mainmod.app) as raw:
        r = raw.post(
            f"/threads/{rid}/messages", json={"message": "still need help"}, headers=_authz(tok)
        )
        assert r.status_code == 200, r.text
        events = _sse_events(r.text)
    assert fake.invokes == 2
    assert "say" in [name for name, _ in events]


def test_resolved_blank_and_ownership_still_enforced(client):
    tok = _login(client, "employee@veridian.local", EMP_PW)
    tok2 = _login(client, "employee2@veridian.local", EMP2_PW)
    atok = _login(client, "agent@veridian.local", AGENT_PW)
    rid = client.post("/threads", json={"message": "keyboard dead"}, headers=_authz(tok)).json()["request_id"]
    assert client.post(f"/threads/{rid}/join", json={}, headers=_authz(atok)).status_code == 200
    assert client.post(f"/threads/{rid}/resolve", json={}, headers=_authz(atok)).status_code == 200
    assert (
        client.post(f"/threads/{rid}/messages", json={"message": "   "}, headers=_authz(tok)).status_code
        == 422
    )
    assert (
        client.post(f"/threads/{rid}/messages", json={"message": "hijack"}, headers=_authz(tok2)).status_code
        == 403
    )
    # Resolved state untouched by the rejected posts.
    detail = client.get(f"/threads/{rid}", headers=_authz(tok)).json()
    assert detail["outcome"] == "resolved"
    assert detail["resolution_source"] == "human"
