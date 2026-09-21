"""Ticket naming: first message names once, before the first reply.

Model-free: the policy model is a scripted fake, the title LLM is either
patched deterministically or forced to fail/malform to exercise the fallback.
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

EMP_PW = "Employee-Pass-01!"
ADMIN_PW = "Admin-Pass-01!!"


class _FakeResponse:
    def __init__(self, tool_calls, content=""):
        self.tool_calls = tool_calls
        self.content = content


class _FakePolicy:
    """Scripted policy model. Each invoke pops the next tool batch."""

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
    monkeypatch.setenv("VERIDIAN_DB", "veridian_it_agent_test_title")
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("JWT_SECRET", "test-secret-for-ticket-title-0123456789")
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


def _convo(text="my laptop won't turn on and stays black") -> Conversation:
    return Conversation(request_id="T-TITLE-1", employee="Aditi", text=text)


def test_first_run_names_before_first_say(monkeypatch):
    monkeypatch.setattr(
        agent_mod, "generate_ticket_title", lambda text: "Laptop won't turn on"
    )
    script = [
        [{"name": "find_policy", "args": {"query": "laptop black screen"}, "id": "c1"}],
        [
            {
                "name": "ask_followup",
                "args": {"question": "How old is the laptop?", "why": "decides rule"},
                "id": "c2",
            }
        ],
    ]
    fake = _FakePolicy(script)
    monkeypatch.setattr(agent_mod, "build_llm", lambda *a, **k: fake)
    convo = _convo()
    events: list[dict] = []
    agent_mod.run(convo, on_event=events.append)

    kinds = [e.get("type") for e in events]
    assert "ticket_updated" in kinds
    first_title = kinds.index("ticket_updated")
    first_say = kinds.index("say")
    assert first_title < first_say, kinds
    title_event = events[first_title]
    assert title_event["title"] == "Laptop won't turn on"
    assert title_event["ticket_title"] == "Laptop won't turn on"
    assert convo.ticket_title == "Laptop won't turn on"
    assert convo.ticket_summary == "Laptop won't turn on"
    # Original employee text kept separately as the description.
    assert convo.text == "my laptop won't turn on and stays black"
    # Normal policy agent still ran.
    assert fake.invokes == 2
    assert convo.outcome == "waiting_on_employee"


def test_title_persisted_and_returned_via_http(client, monkeypatch):
    monkeypatch.setattr(
        agent_mod, "generate_ticket_title", lambda text: "Laptop won't turn on"
    )
    script = [
        [{"name": "find_policy", "args": {"query": "laptop"}, "id": "c1"}],
        [
            {
                "name": "ask_followup",
                "args": {"question": "How old is the laptop?", "why": "decides rule"},
                "id": "c2",
            }
        ],
    ]
    monkeypatch.setattr(agent_mod, "build_llm", lambda *a, **k: _FakePolicy(script))
    tok = _login(client, "employee@veridian.local", EMP_PW)
    created = client.post(
        "/threads", json={"message": "my laptop won't turn on"}, headers=_authz(tok)
    )
    assert created.status_code == 200, created.text
    rid = created.json()["request_id"]
    ticket_id = created.json()["ticket_id"]
    assert ticket_id

    with TestClient(mainmod.app) as raw:
        r = raw.post(
            f"/threads/{rid}/messages",
            json={"message": "my laptop won't turn on"},
            headers=_authz(tok),
        )
        assert r.status_code == 200, r.text
        assert "ticket_updated" in r.text
        assert "Laptop won" in r.text

    detail = client.get(f"/threads/{rid}", headers=_authz(tok)).json()
    assert detail["ticket_title"] == "Laptop won't turn on"
    assert detail["title"] == "Laptop won't turn on"
    assert detail["ticket_id"] == ticket_id
    assert detail["ticket_summary"] == "Laptop won't turn on"

    summaries = client.get("/threads", headers=_authz(tok)).json()
    mine = next(t for t in summaries if t["request_id"] == rid)
    assert mine["ticket_title"] == "Laptop won't turn on"
    assert mine["title"] == "Laptop won't turn on"

    tickets = client.get("/tickets", headers=_authz(tok)).json()
    rows = [t for t in tickets if t.get("request_id") == rid]
    assert len(rows) == 1
    assert rows[0]["ticket_title"] == "Laptop won't turn on"
    assert rows[0]["title"] == "Laptop won't turn on"


def test_second_message_does_not_rename(monkeypatch):
    calls: list[str] = []

    def _title(text: str) -> str:
        calls.append(text)
        return "First title" if len(calls) == 1 else "Second title must not appear"

    monkeypatch.setattr(agent_mod, "generate_ticket_title", _title)
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
    convo = _convo("my laptop won't turn on")
    agent_mod.run(convo, on_event=lambda _e: None)
    assert convo.ticket_title == "First title"
    assert len(calls) == 1

    # Follow-up: policy runs again, but the title hook must not run again.
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
    monkeypatch.setattr(agent_mod, "build_llm", lambda *a, **k: _FakePolicy(second_script))
    convo.say("user", "It is 2 years old")
    convo.text = "It is 2 years old"
    if convo.outcome == "waiting_on_employee":
        convo.outcome = None
    agent_mod.run(convo, on_event=lambda _e: None)
    assert convo.ticket_title == "First title"
    assert len(calls) == 1


def test_failed_title_generation_falls_back_safely(monkeypatch):
    class _Boom:
        def invoke(self, _messages):
            raise RuntimeError("provider down")

    monkeypatch.setattr(agent_mod, "build_llm", lambda *a, **k: _Boom())
    title = agent_mod.generate_ticket_title("my laptop won't turn on at all today")
    assert title
    assert len(title) <= 80
    assert "KB-" not in title
    assert "\n" not in title

    # Malformed paragraph with internal reasoning must also fall back.
    class _Garbage:
        def invoke(self, _messages):
            return _FakeResponse(
                [],
                content=(
                    "KB-03 says eligible after 3 years but ASSET-01 says 4 years; "
                    "conflicting_sources decision analysis needs Finance approval.\n"
                    "Second paragraph of reasoning."
                ),
            )

    monkeypatch.setattr(agent_mod, "build_llm", lambda *a, **k: _Garbage())
    bad = agent_mod.generate_ticket_title("my laptop won't turn on")
    assert bad
    assert "KB-03" not in bad
    assert "conflict" not in bad.lower()
    assert "\n" not in bad

    # Tool-call shaped response (policy script double) falls back too.
    class _ToolShaped:
        invokes = 0
        script = [[{"name": "find_policy", "args": {"query": "x"}, "id": "c1"}]]

        def invoke(self, _messages):
            type(self).invokes += 1
            return _FakeResponse(self.script[0])

    monkeypatch.setattr(agent_mod, "build_llm", lambda *a, **k: _ToolShaped())
    shaped = agent_mod.generate_ticket_title("vpn keeps dropping on my laptop")
    assert shaped
    assert len(shaped) <= 80


def test_policy_still_runs_when_title_llm_fails(monkeypatch):
    # One shared double: title prompt gets a policy tool batch (restored +
    # fallback), policy prompts run their script normally.
    script = [
        [{"name": "find_policy", "args": {"query": "laptop"}, "id": "c1"}],
        [
            {
                "name": "ask_followup",
                "args": {"question": "How old is it?", "why": "decides rule"},
                "id": "c2",
            }
        ],
    ]
    fake = _FakePolicy(script)
    monkeypatch.setattr(agent_mod, "build_llm", lambda *a, **k: fake)
    convo = _convo("vpn keeps dropping every hour on my laptop")
    events: list[dict] = []
    agent_mod.run(convo, on_event=events.append)
    assert convo.ticket_title
    assert len(convo.ticket_title) <= 80
    assert convo.outcome == "waiting_on_employee"
    assert any(a["tool"] == "find_policy" for a in convo.actions)
    assert any(e.get("type") == "say" for e in events)
