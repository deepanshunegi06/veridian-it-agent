"""Regression: handed-off threads stay quiet when the employee writes again.

Once a thread is escalated or joined by human IT, POST /threads/{id}/messages
must persist the employee message and finish the SSE cleanly, but must not
invoke the model or emit an AI `say`. Waiting threads with no human owner
must still reopen and run the AI.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import app.db as dbmod
import app.main as mainmod
from app import agent as agent_mod
from app.auth import create_user_record
from app.llm import get_settings

EMP_PW = "Employee-Pass-01!"
AGENT_PW = "Agent-Pass-01!!"
ADMIN_PW = "Admin-Pass-01!!"


class _FakeResponse:
    def __init__(self, tool_calls, content=""):
        self.tool_calls = tool_calls
        self.content = content


class _FakePolicy:
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
    monkeypatch.setenv("VERIDIAN_DB", "veridian_it_agent_test_handoff")
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("JWT_SECRET", "test-secret-for-handoff-quiet-0123456789")
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
        email="agent@veridian.local", name="Ava Agent", role="it_agent", password=AGENT_PW
    )
    create_user_record(
        email="admin@veridian.local", name="Demo Admin", role="admin", password=ADMIN_PW
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


def _run_first_message(raw: TestClient, rid: str, tok: str) -> str:
    r = raw.post(f"/threads/{rid}/messages", json={"message": "my laptop is dead"}, headers=_authz(tok))
    assert r.status_code == 200, r.text
    return r.text


def test_escalated_thread_followup_does_not_run_ai(client, monkeypatch):
    monkeypatch.setattr(agent_mod, "generate_ticket_title", lambda text: "Laptop issue")
    escalate_script = [
        [
            {
                "name": "escalate",
                "args": {"reason": "policies disagree", "to": "IT lead", "cites": []},
                "id": "c1",
            }
        ],
    ]
    monkeypatch.setattr(agent_mod, "build_llm", lambda *a, **k: _FakePolicy(escalate_script))
    tok = _login(client, "employee@veridian.local", EMP_PW)
    rid = client.post("/threads", json={"message": "my laptop is dead"}, headers=_authz(tok)).json()[
        "request_id"
    ]
    with TestClient(mainmod.app) as raw:
        _run_first_message(raw, rid, tok)
    assert client.get(f"/threads/{rid}", headers=_authz(tok)).json()["outcome"] == "escalated"
    agent_msgs_before = [
        m
        for m in client.get(f"/threads/{rid}", headers=_authz(tok)).json()["messages"]
        if m["speaker"] == "agent"
    ]

    calls: list[int] = []

    def _boom(*a, **k):
        calls.append(1)
        raise AssertionError("model must not run after handoff")

    monkeypatch.setattr(agent_mod, "build_llm", _boom)
    with TestClient(mainmod.app) as raw:
        r = raw.post(f"/threads/{rid}/messages", json={"message": "hi"}, headers=_authz(tok))
        assert r.status_code == 200, r.text
        events = _sse_events(r.text)
    assert calls == []
    kinds = [name for name, _ in events]
    assert "say" not in kinds
    assert "start" in kinds and "done" in kinds
    detail = client.get(f"/threads/{rid}", headers=_authz(tok)).json()
    assert detail["outcome"] == "escalated"
    users = [m for m in detail["messages"] if m["speaker"] == "user"]
    assert users and users[-1]["text"] == "hi"
    agents = [m for m in detail["messages"] if m["speaker"] == "agent"]
    assert len(agents) == len(agent_msgs_before)


def test_assigned_thread_followup_does_not_run_ai_but_human_can_reply(client, monkeypatch):
    monkeypatch.setattr(agent_mod, "generate_ticket_title", lambda text: "Laptop issue")
    followup_script = [
        [{"name": "find_policy", "args": {"query": "laptop"}, "id": "c1"}],
        [
            {
                "name": "ask_followup",
                "args": {"question": "How old is it?", "why": "decides rule"},
                "id": "c2",
            }
        ],
    ]
    monkeypatch.setattr(agent_mod, "build_llm", lambda *a, **k: _FakePolicy(followup_script))
    tok = _login(client, "employee@veridian.local", EMP_PW)
    atok = _login(client, "agent@veridian.local", AGENT_PW)
    rid = client.post("/threads", json={"message": "my laptop is dead"}, headers=_authz(tok)).json()[
        "request_id"
    ]
    with TestClient(mainmod.app) as raw:
        _run_first_message(raw, rid, tok)
    assert client.get(f"/threads/{rid}", headers=_authz(tok)).json()["outcome"] == "waiting_on_employee"

    joined = client.post(f"/threads/{rid}/join", json={}, headers=_authz(atok))
    assert joined.status_code == 200, joined.text
    agent_count_before = len(
        [
            m
            for m in client.get(f"/threads/{rid}", headers=_authz(atok)).json()["messages"]
            if m["speaker"] == "agent"
        ]
    )

    calls: list[int] = []

    def _boom(*a, **k):
        calls.append(1)
        raise AssertionError("model must not run after human join")

    monkeypatch.setattr(agent_mod, "build_llm", _boom)
    with TestClient(mainmod.app) as raw:
        r = raw.post(f"/threads/{rid}/messages", json={"message": "hi"}, headers=_authz(tok))
        assert r.status_code == 200, r.text
        events = _sse_events(r.text)
    assert calls == []
    assert "say" not in [name for name, _ in events]
    detail = client.get(f"/threads/{rid}", headers=_authz(tok)).json()
    assert detail["assigned_to"] == "Ava Agent"
    assert [m for m in detail["messages"] if m["speaker"] == "user"][-1]["text"] == "hi"
    assert (
        len([m for m in detail["messages"] if m["speaker"] == "agent"]) == agent_count_before
    )

    # The assigned human can still reply.
    reply = client.post(
        f"/threads/{rid}/human", json={"message": "On it, checking now."}, headers=_authz(atok)
    )
    assert reply.status_code == 200, reply.text
    assert reply.json()["message"]["speaker"] == "human"


def test_waiting_thread_without_human_still_runs_ai(client, monkeypatch):
    monkeypatch.setattr(agent_mod, "generate_ticket_title", lambda text: "Laptop issue")
    first_script = [
        [{"name": "find_policy", "args": {"query": "laptop"}, "id": "c1"}],
        [
            {
                "name": "ask_followup",
                "args": {"question": "How old is it?", "why": "decides rule"},
                "id": "c2",
            }
        ],
    ]
    monkeypatch.setattr(agent_mod, "build_llm", lambda *a, **k: _FakePolicy(first_script))
    tok = _login(client, "employee@veridian.local", EMP_PW)
    rid = client.post("/threads", json={"message": "my laptop is dead"}, headers=_authz(tok)).json()[
        "request_id"
    ]
    with TestClient(mainmod.app) as raw:
        _run_first_message(raw, rid, tok)
    assert client.get(f"/threads/{rid}", headers=_authz(tok)).json()["outcome"] == "waiting_on_employee"

    second_script = [
        [{"name": "find_policy", "args": {"query": "laptop 2 years"}, "id": "c3"}],
        [
            {
                "name": "ask_followup",
                "args": {"question": "What model?", "why": "narrows rule"},
                "id": "c4",
            }
        ],
    ]
    second_fake = _FakePolicy(second_script)
    monkeypatch.setattr(agent_mod, "build_llm", lambda *a, **k: second_fake)
    with TestClient(mainmod.app) as raw:
        r = raw.post(
            f"/threads/{rid}/messages",
            json={"message": "It is 2 years old"},
            headers=_authz(tok),
        )
        assert r.status_code == 200, r.text
        events = _sse_events(r.text)
    assert second_fake.invokes == 2
    assert "say" in [name for name, _ in events]
    detail = client.get(f"/threads/{rid}", headers=_authz(tok)).json()
    assert detail["outcome"] == "waiting_on_employee"
