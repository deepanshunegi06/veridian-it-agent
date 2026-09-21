"""Regression: follow-up pauses the run; no duplicate bubbles; waiting continues.

Covers the two employee-chat failures without a model in the room:

- ``agent.run`` must stop immediately after a successful ``ask_followup``
  (outcome ``waiting_on_employee``), emitting exactly one ``say`` with the
  canonical ``seq``/``message_id`` and never calling ``escalate`` or any
  later tool.
- HTTP: the first employee message stores exactly one user message plus one
  agent follow-up (no duplicate stored messages after stream/reload).
- A new employee message after waiting reopens the thread and runs again
  instead of being blocked or incorrectly escalated.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.db as dbmod
import app.main as mainmod
from app import agent as agent_mod
from app.auth import create_user_record
from app.llm import get_settings
from app.tools import Conversation


class _FakeResponse:
    def __init__(self, tool_calls, content=""):
        self.tool_calls = tool_calls
        self.content = content


class _FakeModel:
    """Scripted tool-call model. Each ``invoke`` pops the next batch."""

    def __init__(self, script):
        self.script = [list(batch) for batch in script]
        self.invokes = 0

    def bind_tools(self, _schemas):
        return self

    def invoke(self, _messages):
        if self.invokes < len(self.script):
            batch = self.script[self.invokes]
        else:
            # Any extra step is the bug: the loop continued past follow-up.
            batch = [
                {
                    "name": "escalate",
                    "args": {"reason": "should not happen", "to": "IT lead", "cites": []},
                    "id": f"extra-{self.invokes}",
                }
            ]
        self.invokes += 1
        return _FakeResponse(batch)


def _convo(text="my laptop is dead, it is about 3 years old") -> Conversation:
    return Conversation(request_id="T-FOLLOWUP", employee="Aditi", text=text)


def test_followup_pauses_run_with_single_message_and_no_escalation(monkeypatch):
    script = [
        [{"name": "find_policy", "args": {"query": "laptop age replacement"}, "id": "c1"}],
        [
            {
                "name": "ask_followup",
                "args": {"question": "How old is the laptop?", "why": "decides replacement rule"},
                "id": "c2",
            }
        ],
    ]
    fake = _FakeModel(script)
    monkeypatch.setattr(agent_mod, "build_llm", lambda *a, **k: fake)

    convo = _convo()
    events: list[dict] = []
    agent_mod.run(convo, on_event=events.append)

    assert fake.invokes == 2, f"loop continued past follow-up: {fake.invokes} invokes"
    assert convo.outcome == "waiting_on_employee"
    assert convo.followups_asked == 1

    agent_msgs = [m for m in convo.messages if m["speaker"] == "agent"]
    assert len(agent_msgs) == 1
    assert agent_msgs[0]["text"] == "How old is the laptop?"
    # Canonical seq: user seq 0 at creation, follow-up seq 1.
    assert agent_msgs[0]["seq"] == 1
    assert convo.messages[0]["seq"] == 0

    tools_called = [a["tool"] for a in convo.actions if "refused" not in a]
    assert tools_called == ["find_policy", "ask_followup"]
    assert not any(a["tool"] == "escalate" for a in convo.actions)

    assert convo.turns == [{"speaker": "agent", "text": "How old is the laptop?"}]

    says = [e for e in events if e.get("type") == "say"]
    assert len(says) == 1
    assert says[0]["text"] == "How old is the laptop?"
    assert says[0]["seq"] == 1
    assert says[0]["message_id"] == "m-1"

    outcomes = [e for e in events if e.get("type") == "outcome"]
    assert outcomes and outcomes[-1]["outcome"] == "waiting_on_employee"


def test_followup_in_same_batch_ignores_later_calls(monkeypatch):
    """A batch of [ask_followup, escalate] must not run the trailing call."""
    script = [
        [
            {
                "name": "ask_followup",
                "args": {"question": "Which laptop is it?", "why": "decides rule"},
                "id": "c1",
            },
            {
                "name": "escalate",
                "args": {"reason": "should be ignored", "to": "IT lead", "cites": []},
                "id": "c2",
            },
        ],
    ]
    fake = _FakeModel(script)
    monkeypatch.setattr(agent_mod, "build_llm", lambda *a, **k: fake)

    convo = _convo()
    events: list[dict] = []
    agent_mod.run(convo, on_event=events.append)

    assert fake.invokes == 1
    assert convo.outcome == "waiting_on_employee"
    assert len([m for m in convo.messages if m["speaker"] == "agent"]) == 1
    assert not any(a["tool"] == "escalate" for a in convo.actions)


def test_refused_followup_does_not_pause(monkeypatch):
    """Budget-spent follow-up is a refusal: the loop must keep working."""
    convo = _convo()
    # Spend the budget directly (no model involved).
    from app import tools as tools_mod

    assert "asked" in tools_mod.ask_followup(convo, "Q1?", "why")
    assert "asked" in tools_mod.ask_followup(convo, "Q2?", "why")
    assert convo.followups_asked == 2

    script = [
        [
            {
                "name": "ask_followup",
                "args": {"question": "Q3?", "why": "still narrowing"},
                "id": "c1",
            }
        ],
        [
            {
                "name": "escalate",
                "args": {"reason": "need a person", "to": "IT lead", "cites": []},
                "id": "c2",
            }
        ],
    ]
    fake = _FakeModel(script)
    monkeypatch.setattr(agent_mod, "build_llm", lambda *a, **k: fake)
    events: list[dict] = []
    agent_mod.run(convo, on_event=events.append)

    # Refused follow-up did not pause; the later escalate ran and closed it.
    assert convo.outcome == "escalated"
    assert any(a["tool"] == "escalate" for a in convo.actions)
    assert fake.invokes == 2


# --- HTTP: canonical storage + waiting continuation ---------------------------

EMP_PW = "Employee-Pass-01!"
ADMIN_PW = "Admin-Pass-01!!"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("MONGODB_URI", "")
    monkeypatch.setenv("VERIDIAN_DB", "veridian_it_agent_test")
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("JWT_SECRET", "test-secret-for-followup-regression-0123456789")
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


def test_first_message_stores_single_user_and_single_followup_no_duplicates(client, monkeypatch):
    first_script = [
        [{"name": "find_policy", "args": {"query": "laptop replacement age"}, "id": "c1"}],
        [
            {
                "name": "ask_followup",
                "args": {"question": "How old is the laptop?", "why": "decides replacement rule"},
                "id": "c2",
            }
        ],
    ]
    first_fake = _FakeModel(first_script)
    monkeypatch.setattr(agent_mod, "build_llm", lambda *a, **k: first_fake)

    tok = _login(client, "employee@veridian.local", EMP_PW)
    created = client.post(
        "/threads", json={"message": "my laptop is dead"}, headers=_authz(tok)
    )
    assert created.status_code == 200, created.text
    rid = created.json()["request_id"]
    assert len(created.json()["messages"]) == 1

    # Re-post the creation text to trigger the run (product flow). The guard
    # must not store it twice.
    with TestClient(mainmod.app) as raw:
        r = raw.post(f"/threads/{rid}/messages", json={"message": "my laptop is dead"}, headers=_authz(tok))
        assert r.status_code == 200, r.text
        # Drain the SSE stream so persistence completes.
        _ = r.text

    assert first_fake.invokes == 2, "agent should stop after the follow-up, not escalate"

    detail = client.get(f"/threads/{rid}", headers=_authz(tok)).json()
    users = [m for m in detail["messages"] if m["speaker"] == "user"]
    agents = [m for m in detail["messages"] if m["speaker"] == "agent"]
    assert len(users) == 1, users
    assert len(agents) == 1, agents
    assert agents[0]["text"] == "How old is the laptop?"
    assert detail["outcome"] == "waiting_on_employee"
    # Stable seqs: user m-0, follow-up m-1.
    assert [m["seq"] for m in detail["messages"]] == [0, 1]
    assert detail["message_count"] == 2
    assert not any(a["tool"] == "escalate" for a in detail["actions"])

    # Reload from persistence (cache clear): still exactly two messages.
    mainmod.CONVERSATIONS._cache.clear()
    reloaded = client.get(f"/threads/{rid}", headers=_authz(tok)).json()
    assert len(reloaded["messages"]) == 2
    assert [m["seq"] for m in reloaded["messages"]] == [0, 1]
    assert reloaded["outcome"] == "waiting_on_employee"


def test_message_after_waiting_continues_thread(client, monkeypatch):
    first_script = [
        [{"name": "find_policy", "args": {"query": "laptop"}, "id": "c1"}],
        [
            {
                "name": "ask_followup",
                "args": {"question": "How old is the laptop?", "why": "decides rule"},
                "id": "c2",
            }
        ],
    ]
    monkeypatch.setattr(agent_mod, "build_llm", lambda *a, **k: _FakeModel(first_script))
    tok = _login(client, "employee@veridian.local", EMP_PW)
    rid = client.post("/threads", json={"message": "my laptop is dead"}, headers=_authz(tok)).json()[
        "request_id"
    ]
    with TestClient(mainmod.app) as raw:
        r = raw.post(f"/threads/{rid}/messages", json={"message": "my laptop is dead"}, headers=_authz(tok))
        assert r.status_code == 200, r.text
        _ = r.text
    assert client.get(f"/threads/{rid}", headers=_authz(tok)).json()["outcome"] == "waiting_on_employee"

    # Employee answers: the thread must reopen and the agent must run again
    # (not 409/403, not a silent waiting return, not a spurious escalation).
    second_script = [
        [{"name": "find_policy", "args": {"query": "laptop 2 years old"}, "id": "c3"}],
        [
            {
                "name": "ask_followup",
                "args": {"question": "What model is it?", "why": "narrows the rule"},
                "id": "c4",
            }
        ],
    ]
    second_fake = _FakeModel(second_script)
    monkeypatch.setattr(agent_mod, "build_llm", lambda *a, **k: second_fake)
    with TestClient(mainmod.app) as raw:
        r2 = raw.post(
            f"/threads/{rid}/messages", json={"message": "It is 2 years old, XPS 13"}, headers=_authz(tok)
        )
        assert r2.status_code == 200, r2.text
        _ = r2.text

    assert second_fake.invokes == 2, "second run should do real work, not return waiting immediately"
    detail = client.get(f"/threads/{rid}", headers=_authz(tok)).json()
    users = [m for m in detail["messages"] if m["speaker"] == "user"]
    agents = [m for m in detail["messages"] if m["speaker"] == "agent"]
    assert len(users) == 2, users
    assert len(agents) == 2, agents
    assert agents[1]["text"] == "What model is it?"
    assert detail["outcome"] == "waiting_on_employee"
    assert [m["seq"] for m in detail["messages"]] == [0, 1, 2, 3]
    assert not any(a["tool"] == "escalate" for a in detail["actions"])
