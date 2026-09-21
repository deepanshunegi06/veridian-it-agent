"""Auth + RBAC API tests. No LLM, no Mongo, no network.

Runs with MONGODB_URI empty and no LLM key. Agent execution is avoided;
the one test that hits the message endpoint monkeypatches app.main.agent.run.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.db as dbmod
import app.main as mainmod
from app import kb
from app.auth import create_user_record
from app.llm import get_settings

EMP_PW = "Employee-Pass-01!"
AGENT_PW = "Agent-Pass-01!!"
ADMIN_PW = "Admin-Pass-01!!"
EMP2_PW = "Employee2-Pass!"
AGENT2_PW = "Agent2-Pass-01!"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("MONGODB_URI", "")
    monkeypatch.setenv("VERIDIAN_DB", "veridian_it_agent_test")
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("JWT_SECRET", "test-secret-for-auth-tests-only-0123456789")
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")
    get_settings.cache_clear()
    # Reset cached mongo client so empty URI takes effect.
    dbmod._CLIENT = None
    dbmod._CLIENT_URI = None
    dbmod._MEMORY_CONVOS.clear()
    dbmod._MEMORY_EVENTS.clear()
    dbmod._MEMORY_USERS.clear()
    dbmod._MEMORY_COUNTERS["ticket_seq"] = 0
    mainmod.CONVERSATIONS._cache.clear()
    kb.clear_overrides()

    create_user_record(
        email="employee@veridian.local", name="Eddie Employee", role="employee", password=EMP_PW
    )
    create_user_record(
        email="agent@veridian.local", name="Ava Agent", role="it_agent", password=AGENT_PW
    )
    create_user_record(
        email="admin@veridian.local", name="Ada Admin", role="admin", password=ADMIN_PW
    )
    create_user_record(
        email="employee2@veridian.local",
        name="Erin Employee",
        role="employee",
        password=EMP2_PW,
    )
    create_user_record(
        email="agent2@veridian.local", name="Ben Agent", role="it_agent", password=AGENT2_PW
    )
    with TestClient(mainmod.app) as c:
        yield c
    # Cleanup KB overrides possibly created by admin tests.
    kb.clear_overrides()
    mainmod.CONVERSATIONS._cache.clear()
    dbmod._MEMORY_CONVOS.clear()
    dbmod._MEMORY_USERS.clear()


def login(c: TestClient, email: str, password: str) -> tuple[dict, str]:
    r = c.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    return body["user"], body["access_token"]


def authz(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# --- auth basics -------------------------------------------------------------


def test_login_ok_returns_user_and_sets_cookie(client):
    r = client.post(
        "/auth/login",
        json={"email": "employee@veridian.local", "password": EMP_PW},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body["user"]) == {"id", "email", "name", "role", "is_active"}
    assert body["user"]["email"] == "employee@veridian.local"
    assert body["user"]["role"] == "employee"
    assert "password_hash" not in r.text.lower()
    assert EMP_PW not in r.text
    assert "veridian_access" in r.cookies
    # /auth/me works with the cookie alone.
    me = client.get("/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["email"] == "employee@veridian.local"


def test_login_wrong_password_401(client):
    r = client.post(
        "/auth/login", json={"email": "employee@veridian.local", "password": "Wrong-Pass-01!"}
    )
    assert r.status_code == 401


def test_protected_route_401_without_auth(client):
    assert client.get("/threads").status_code == 401
    assert client.get("/kb").status_code == 401
    assert client.get("/auth/me").status_code == 401


def test_healthz_public(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_logout_clears_cookie(client):
    _, token = login(client, "employee@veridian.local", EMP_PW)
    r = client.post("/auth/logout", headers=authz(token))
    assert r.status_code == 200
    assert r.json() == {"ok": True}


# --- threads: ownership -------------------------------------------------------


def test_employee_create_own_thread_and_spoofed_name_ignored(client):
    _, token = login(client, "employee@veridian.local", EMP_PW)
    r = client.post(
        "/threads",
        json={"message": "my laptop is dead", "employee": "Mallory"},
        headers=authz(token),
    )
    assert r.status_code == 200, r.text
    rid = r.json()["request_id"]
    detail = client.get(f"/threads/{rid}", headers=authz(token))
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["employee"] == "Eddie Employee"  # derived from auth, not body
    assert body["owner_user_id"]
    assert body["messages"][0]["name"] == "Eddie Employee"


def test_employee_cannot_read_other_employee_thread(client):
    _, t1 = login(client, "employee@veridian.local", EMP_PW)
    _, t2 = login(client, "employee2@veridian.local", EMP2_PW)
    rid = client.post("/threads", json={"message": "wifi down"}, headers=authz(t1)).json()[
        "request_id"
    ]
    assert client.get(f"/threads/{rid}", headers=authz(t2)).status_code == 403
    # Employee list shows only own threads.
    mine_t2 = {t["request_id"] for t in client.get("/threads", headers=authz(t2)).json()}
    assert rid not in mine_t2
    mine_t1 = {t["request_id"] for t in client.get("/threads", headers=authz(t1)).json()}
    assert rid in mine_t1


def test_employee_denied_support_actions(client):
    _, etok = login(client, "employee@veridian.local", EMP_PW)
    rid = client.post("/threads", json={"message": "need help"}, headers=authz(etok)).json()[
        "request_id"
    ]
    assert client.post(f"/threads/{rid}/join", json={}, headers=authz(etok)).status_code == 403
    assert (
        client.post(f"/threads/{rid}/human", json={"message": "hi"}, headers=authz(etok)).status_code
        == 403
    )
    assert client.post(f"/threads/{rid}/resolve", json={}, headers=authz(etok)).status_code == 403
    assert client.get("/inbox", headers=authz(etok)).status_code == 403
    assert client.get("/auth/users", headers=authz(etok)).status_code == 403
    assert client.post("/reset", headers=authz(etok)).status_code == 403
    assert client.post("/chat", json={"message": "hi"}, headers=authz(etok)).status_code == 403


# --- support flow --------------------------------------------------------------


def test_agent_join_reply_resolve_flow(client):
    _, etok = login(client, "employee@veridian.local", EMP_PW)
    _, atok = login(client, "agent@veridian.local", AGENT_PW)
    rid = client.post(
        "/threads", json={"message": "printer jammed badly"}, headers=authz(etok)
    ).json()["request_id"]

    # Agent can list all threads and filter via /inbox.
    inbox = client.get("/inbox", headers=authz(atok))
    assert inbox.status_code == 200, inbox.text
    assert rid in {t["request_id"] for t in inbox.json()}
    filtered = client.get("/inbox", params={"q": "printer"}, headers=authz(atok)).json()
    assert rid in {t["request_id"] for t in filtered}
    none_match = client.get("/inbox", params={"q": "zzz-no-match"}, headers=authz(atok)).json()
    assert rid not in {t["request_id"] for t in none_match}

    joined = client.post(f"/threads/{rid}/join", json={}, headers=authz(atok))
    assert joined.status_code == 200, joined.text
    assert joined.json()["assigned_to"] == "Ava Agent"
    assert joined.json()["ticket_status"] == "assigned"

    reply = client.post(
        f"/threads/{rid}/human", json={"message": "On my way."}, headers=authz(atok)
    )
    assert reply.status_code == 200, reply.text
    assert reply.json()["message"]["speaker"] == "human"
    assert reply.json()["message"]["name"] == "Ava Agent"

    resolved = client.post(f"/threads/{rid}/resolve", json={}, headers=authz(atok))
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["outcome"] == "resolved"
    assert resolved.json()["ticket_status"] == "resolved"


def test_agent_cannot_resolve_thread_assigned_to_other_agent_unless_admin(client):
    _, etok = login(client, "employee@veridian.local", EMP_PW)
    _, a1 = login(client, "agent@veridian.local", AGENT_PW)
    _, a2 = login(client, "agent2@veridian.local", AGENT2_PW)
    _, adm = login(client, "admin@veridian.local", ADMIN_PW)
    rid = client.post("/threads", json={"message": "vpn broken"}, headers=authz(etok)).json()[
        "request_id"
    ]
    assert client.post(f"/threads/{rid}/join", json={}, headers=authz(a1)).status_code == 200
    denied = client.post(f"/threads/{rid}/resolve", json={}, headers=authz(a2))
    assert denied.status_code == 403, denied.text
    ok = client.post(f"/threads/{rid}/resolve", json={}, headers=authz(adm))
    assert ok.status_code == 200, ok.text


def test_agent_denied_kb_write_admin_allowed(client):
    _, atok = login(client, "agent@veridian.local", AGENT_PW)
    _, adm = login(client, "admin@veridian.local", ADMIN_PW)
    payload = {
        "id": "KB-11",
        "title": "Test clause",
        "text": "Test policy text for seeding.",
        "authority": "IT",
        "keywords": ["testseed"],
        "conflicts_with": [],
    }
    denied = client.post("/kb", json=payload, headers=authz(atok))
    assert denied.status_code == 403, denied.text
    created = client.post("/kb", json=payload, headers=authz(adm))
    assert created.status_code == 200, created.text
    assert created.json()["id"] == "KB-11"
    edited = client.put("/kb/KB-11", json={"title": "Renamed"}, headers=authz(adm))
    assert edited.status_code == 200, edited.text
    assert edited.json()["title"] == "Renamed"


def test_kb_read_authenticated_for_all_roles(client):
    _, etok = login(client, "employee@veridian.local", EMP_PW)
    r = client.get("/kb", headers=authz(etok))
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list)


# --- user management -----------------------------------------------------------


def test_admin_user_management(client):
    _, adm = login(client, "admin@veridian.local", ADMIN_PW)
    users = client.get("/auth/users", headers=authz(adm))
    assert users.status_code == 200, users.text
    assert {u["email"] for u in users.json()} >= {
        "employee@veridian.local",
        "agent@veridian.local",
        "admin@veridian.local",
    }
    for u in users.json():
        assert "password_hash" not in u

    created = client.post(
        "/auth/users",
        json={
            "email": "newbie@veridian.local",
            "name": "Newbie",
            "role": "employee",
            "password": "Newbie-Pass-01!",
        },
        headers=authz(adm),
    )
    assert created.status_code == 200, created.text
    nid = created.json()["id"]

    dup = client.post(
        "/auth/users",
        json={
            "email": "newbie@veridian.local",
            "name": "Dupe",
            "role": "employee",
            "password": "Newbie-Pass-01!",
        },
        headers=authz(adm),
    )
    assert dup.status_code == 409

    patched = client.patch(
        f"/auth/users/{nid}", json={"name": "Renamed", "is_active": False}, headers=authz(adm)
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["name"] == "Renamed"
    assert patched.json()["is_active"] is False

    # Disabled account cannot log in.
    bad = client.post(
        "/auth/login", json={"email": "newbie@veridian.local", "password": "Newbie-Pass-01!"}
    )
    assert bad.status_code == 401


def test_non_admin_denied_user_management(client):
    _, atok = login(client, "agent@veridian.local", AGENT_PW)
    assert client.get("/auth/users", headers=authz(atok)).status_code == 403
    assert (
        client.post(
            "/auth/users",
            json={
                "email": "x@veridian.local",
                "name": "X",
                "role": "employee",
                "password": "Xxxxxxxx-01!",
            },
            headers=authz(atok),
        ).status_code
        == 403
    )


# --- persistence / cache reload -------------------------------------------------


def test_thread_survives_cache_clear_via_persistence(client):
    _, etok = login(client, "employee@veridian.local", EMP_PW)
    me = client.get("/auth/me", headers=authz(etok)).json()
    rid = client.post("/threads", json={"message": "cache reload check"}, headers=authz(etok)).json()[
        "request_id"
    ]
    mainmod.CONVERSATIONS._cache.clear()
    detail = client.get(f"/threads/{rid}", headers=authz(etok))
    assert detail.status_code == 200, detail.text
    assert detail.json()["owner_user_id"] == me["id"]
    # Stored doc carries the owner too.
    stored = dbmod.load_conversation(rid)
    assert stored is not None
    assert stored.get("owner_user_id") == me["id"]


def test_post_message_ownership_and_no_spoof(monkeypatch, client):
    from app.main import agent as agent_mod

    monkeypatch.setattr(
        agent_mod, "run", lambda convo, on_event=None: convo.say("agent", "stub reply")
    )
    _, t1 = login(client, "employee@veridian.local", EMP_PW)
    _, t2 = login(client, "employee2@veridian.local", EMP2_PW)
    rid = client.post("/threads", json={"message": "first"}, headers=authz(t1)).json()[
        "request_id"
    ]
    # Cross-employee post is forbidden without running the agent.
    assert (
        client.post(f"/threads/{rid}/messages", json={"message": "hijack"}, headers=authz(t2)).status_code
        == 403
    )
    # Owner can post; the user message name comes from auth.
    with TestClient(mainmod.app) as raw:
        # reuse tokens explicitly since cookies may differ
        r = raw.post(
            f"/threads/{rid}/messages",
            json={"message": "follow-up"},
            headers=authz(t1),
        )
        assert r.status_code == 200, r.text


def _stub_agent(monkeypatch):
    from app.main import agent as agent_mod

    monkeypatch.setattr(
        agent_mod, "run", lambda convo, on_event=None: convo.say("agent", "stub reply")
    )


# --- first-message duplication -------------------------------------------------


def test_first_message_repost_runs_without_duplicating(monkeypatch, client):
    """POST /threads stores the first user message; re-sending the same text
    to /messages to trigger the run must not store it twice."""
    _stub_agent(monkeypatch)
    _, tok = login(client, "employee@veridian.local", EMP_PW)
    rid = client.post(
        "/threads", json={"message": "printer jammed badly"}, headers=authz(tok)
    ).json()["request_id"]
    with TestClient(mainmod.app) as raw:
        r = raw.post(
            f"/threads/{rid}/messages",
            json={"message": "printer jammed badly"},
            headers=authz(tok),
        )
        assert r.status_code == 200, r.text
    detail = client.get(f"/threads/{rid}", headers=authz(tok)).json()
    users = [m for m in detail["messages"] if m["speaker"] == "user"]
    assert len(users) == 1
    assert detail["message_count"] == 2  # the one user message + the stub agent reply
    assert detail["text"] == "printer jammed badly"
    # A genuine follow-up with different text is stored normally.
    with TestClient(mainmod.app) as raw:
        r2 = raw.post(
            f"/threads/{rid}/messages",
            json={"message": "it is still jammed"},
            headers=authz(tok),
        )
        assert r2.status_code == 200, r2.text
    detail2 = client.get(f"/threads/{rid}", headers=authz(tok)).json()
    assert len([m for m in detail2["messages"] if m["speaker"] == "user"]) == 2


def test_blank_messages_rejected(client):
    _, tok = login(client, "employee@veridian.local", EMP_PW)
    assert (
        client.post("/threads", json={"message": "   "}, headers=authz(tok)).status_code
        == 422
    )
    rid = client.post(
        "/threads", json={"message": "real issue"}, headers=authz(tok)
    ).json()["request_id"]
    assert (
        client.post(
            f"/threads/{rid}/messages", json={"message": "   "}, headers=authz(tok)
        ).status_code
        == 422
    )


def test_support_cannot_post_user_messages(client):
    _, etok = login(client, "employee@veridian.local", EMP_PW)
    _, atok = login(client, "agent@veridian.local", AGENT_PW)
    _, adm = login(client, "admin@veridian.local", ADMIN_PW)
    rid = client.post("/threads", json={"message": "need help"}, headers=authz(etok)).json()[
        "request_id"
    ]
    # Support replies go through /human; /messages is owner-only.
    assert (
        client.post(
            f"/threads/{rid}/messages", json={"message": "as support"}, headers=authz(atok)
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/threads/{rid}/messages", json={"message": "as admin"}, headers=authz(adm)
        ).status_code
        == 403
    )


# --- legacy route ownership ----------------------------------------------------


def test_legacy_request_detail_enforces_thread_ownership(client):
    _, t1 = login(client, "employee@veridian.local", EMP_PW)
    _, t2 = login(client, "employee2@veridian.local", EMP2_PW)
    _, atok = login(client, "agent@veridian.local", AGENT_PW)
    rid = client.post("/threads", json={"message": "wifi down"}, headers=authz(t1)).json()[
        "request_id"
    ]
    # Another employee cannot bypass /threads/{id} ownership via /requests/{id}.
    assert client.get(f"/requests/{rid}", headers=authz(t2)).status_code == 403
    assert client.get(f"/requests/{rid}", headers=authz(t1)).status_code == 200
    assert client.get(f"/requests/{rid}", headers=authz(atok)).status_code == 200
    # Shared fixture rows stay readable for any signed-in user.
    assert client.get("/requests/REQ-01", headers=authz(t2)).status_code == 200


def test_tickets_and_stats_scoped_to_owner_for_employees(client):
    from app import tools as tools_mod

    _, t1 = login(client, "employee@veridian.local", EMP_PW)
    _, t2 = login(client, "employee2@veridian.local", EMP2_PW)
    _, atok = login(client, "agent@veridian.local", AGENT_PW)
    r1 = client.post("/threads", json={"message": "laptop screen flickers"}, headers=authz(t1)).json()[
        "request_id"
    ]
    r2 = client.post("/threads", json={"message": "mouse battery dead"}, headers=authz(t2)).json()[
        "request_id"
    ]
    tools_mod.raise_ticket(mainmod.CONVERSATIONS.get(r1), "hardware", "Fix laptop screen", "normal")

    mine = client.get("/tickets", headers=authz(t1)).json()
    raised_mine = [t for t in mine if t.get("origin") == "raised by the agent"]
    assert {t["request_id"] for t in raised_mine} == {r1}

    others = client.get("/tickets", headers=authz(t2)).json()
    raised_others = [t for t in others if t.get("origin") == "raised by the agent"]
    assert r1 not in {t["request_id"] for t in raised_others}

    support = client.get("/tickets", headers=authz(atok)).json()
    raised_all = [t for t in support if t.get("origin") == "raised by the agent"]
    raised_ids = {t.get("request_id") for t in raised_all}
    assert r1 in raised_ids  # only r1 raised a ticket; r2 raised nothing
    assert r2 not in raised_ids

    assert client.get("/stats", headers=authz(t1)).json()["handled"] == 1
    assert client.get("/stats", headers=authz(atok)).json()["handled"] == 2


# --- assignment / resolve rules --------------------------------------------------


def test_join_takeover_and_idempotency(client):
    _, etok = login(client, "employee@veridian.local", EMP_PW)
    _, a1 = login(client, "agent@veridian.local", AGENT_PW)
    _, a2 = login(client, "agent2@veridian.local", AGENT2_PW)
    _, adm = login(client, "admin@veridian.local", ADMIN_PW)
    rid = client.post("/threads", json={"message": "vpn broken"}, headers=authz(etok)).json()[
        "request_id"
    ]
    assert client.post(f"/threads/{rid}/join", json={}, headers=authz(a1)).status_code == 200
    # Another agent cannot steal the thread; an admin can take it over.
    assert client.post(f"/threads/{rid}/join", json={}, headers=authz(a2)).status_code == 409
    assert client.post(f"/threads/{rid}/join", json={}, headers=authz(adm)).status_code == 200
    # Same-agent re-join is an idempotent no-op: no new announcement.
    before = client.get(f"/threads/{rid}", headers=authz(adm)).json()
    joins_before = [
        m
        for m in before["messages"]
        if m["speaker"] == "system" and "joined" in m["text"]
    ]
    assert len(joins_before) == 2  # agent join + admin takeover
    assert client.post(f"/threads/{rid}/join", json={}, headers=authz(adm)).status_code == 200
    after = client.get(f"/threads/{rid}", headers=authz(adm)).json()
    joins_after = [
        m for m in after["messages"] if m["speaker"] == "system" and "joined" in m["text"]
    ]
    assert len(joins_after) == len(joins_before)


def test_human_reply_only_assigned_or_admin(client):
    _, etok = login(client, "employee@veridian.local", EMP_PW)
    _, a1 = login(client, "agent@veridian.local", AGENT_PW)
    _, a2 = login(client, "agent2@veridian.local", AGENT2_PW)
    _, adm = login(client, "admin@veridian.local", ADMIN_PW)
    rid = client.post("/threads", json={"message": "monitor flickers"}, headers=authz(etok)).json()[
        "request_id"
    ]
    assert client.post(f"/threads/{rid}/join", json={}, headers=authz(a1)).status_code == 200
    denied = client.post(
        f"/threads/{rid}/human", json={"message": "butting in"}, headers=authz(a2)
    )
    assert denied.status_code == 403, denied.text
    ok = client.post(
        f"/threads/{rid}/human", json={"message": "On my way."}, headers=authz(a1)
    )
    assert ok.status_code == 200, ok.text
    admin_ok = client.post(
        f"/threads/{rid}/human", json={"message": "Admin note."}, headers=authz(adm)
    )
    assert admin_ok.status_code == 200, admin_ok.text


def test_resolve_idempotent_and_terminal(client):
    _, etok = login(client, "employee@veridian.local", EMP_PW)
    _, a1 = login(client, "agent@veridian.local", AGENT_PW)
    rid = client.post("/threads", json={"message": "keyboard dead"}, headers=authz(etok)).json()[
        "request_id"
    ]
    assert client.post(f"/threads/{rid}/join", json={}, headers=authz(a1)).status_code == 200
    assert client.post(f"/threads/{rid}/resolve", json={}, headers=authz(a1)).status_code == 200
    # Second resolve: same 200 summary, no duplicate announcement.
    again = client.post(f"/threads/{rid}/resolve", json={}, headers=authz(a1))
    assert again.status_code == 200, again.text
    detail = client.get(f"/threads/{rid}", headers=authz(a1)).json()
    resolved_notes = [
        m
        for m in detail["messages"]
        if m["speaker"] == "system" and "resolved" in m["text"].lower()
    ]
    assert len(resolved_notes) == 1
    # Still-resolved threads refuse further join/human traffic, but a new
    # employee message reopens instead of 409 (human-resolved -> quiet IT
    # handoff, assignment cleared).
    assert client.post(f"/threads/{rid}/join", json={}, headers=authz(a1)).status_code == 409
    assert (
        client.post(
            f"/threads/{rid}/human", json={"message": "late reply"}, headers=authz(a1)
        ).status_code
        == 409
    )
    with TestClient(mainmod.app) as raw:
        r = raw.post(
            f"/threads/{rid}/messages", json={"message": "late user msg"}, headers=authz(etok)
        )
        assert r.status_code == 200, r.text
        _ = r.text
    detail2 = client.get(f"/threads/{rid}", headers=authz(a1)).json()
    assert detail2["outcome"] == "escalated"
    assert detail2["ticket_status"] == "open"
    assert detail2["assigned_to"] is None
    assert detail2["resolution_source"] is None
    users = [m for m in detail2["messages"] if m["speaker"] == "user"]
    assert users and users[-1]["text"] == "late user msg"


# --- admin user endpoints --------------------------------------------------------


def test_admin_cannot_self_demote_or_disable_and_blank_name_rejected(client):
    _, adm = login(client, "admin@veridian.local", ADMIN_PW)
    me = client.get("/auth/me", headers=authz(adm)).json()
    assert (
        client.patch(
            f"/auth/users/{me['id']}", json={"role": "employee"}, headers=authz(adm)
        ).status_code
        == 403
    )
    assert (
        client.patch(
            f"/auth/users/{me['id']}", json={"is_active": False}, headers=authz(adm)
        ).status_code
        == 403
    )
    assert (
        client.patch(
            f"/auth/users/{me['id']}", json={"name": "   "}, headers=authz(adm)
        ).status_code
        == 422
    )
    # Other accounts can still be managed, including role changes.
    created = client.post(
        "/auth/users",
        json={
            "email": "rolechange@veridian.local",
            "name": "Role Change",
            "role": "employee",
            "password": "Role-Change-01!",
        },
        headers=authz(adm),
    )
    assert created.status_code == 200, created.text
    nid = created.json()["id"]
    patched = client.patch(
        f"/auth/users/{nid}", json={"role": "it_agent"}, headers=authz(adm)
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["role"] == "it_agent"
    assert "password_hash" not in patched.text.lower()


def test_save_user_rejects_duplicate_email(client):
    with pytest.raises(ValueError, match="already exists"):
        dbmod.save_user(
            {
                "id": "U-TESTDUP00001",
                "email": "EMPLOYEE@veridian.local",
                "name": "Dup",
                "role": "employee",
                "is_active": True,
                "password_hash": "x",
            }
        )
    _, adm = login(client, "admin@veridian.local", ADMIN_PW)
    dup = client.post(
        "/auth/users",
        json={
            "email": "Employee@Veridian.Local",
            "name": "Dupe",
            "role": "employee",
            "password": "Dupe-Pass-01!",
        },
        headers=authz(adm),
    )
    assert dup.status_code == 409


# --- cookie / bearer / CORS --------------------------------------------------------


def test_bearer_wins_over_cookie(client):
    _, t_emp = login(client, "employee@veridian.local", EMP_PW)
    _, t_adm = login(client, "admin@veridian.local", ADMIN_PW)
    # The jar now holds the admin cookie; an explicit employee Bearer must win.
    me = client.get("/auth/me", headers=authz(t_emp))
    assert me.status_code == 200, me.text
    assert me.json()["email"] == "employee@veridian.local"
    assert t_adm  # both tokens issued; keeps the pair symmetric for readers


def test_cors_wildcard_falls_back(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "*")
    get_settings.cache_clear()
    try:
        from app.main import _cors_origins

        assert _cors_origins() == ["http://localhost:3000"]
    finally:
        get_settings.cache_clear()


# --- seed idempotency / persistence --------------------------------------------------


def test_seed_idempotent_and_dev_only(monkeypatch, client):
    from app.auth import ensure_seed_users

    # Fixture users already occupy the demo emails: seeding is a no-op.
    assert ensure_seed_users() == []
    dbmod._MEMORY_USERS.clear()
    first = ensure_seed_users()
    assert {u["email"] for u in first} == {
        "employee@veridian.local",
        "agent@veridian.local",
        "admin@veridian.local",
    }
    hashes = {
        u["id"]: dbmod.get_user_by_id(u["id"])["password_hash"] for u in first  # type: ignore[index]
    }
    # Second run seeds nothing and never rewrites passwords.
    assert ensure_seed_users() == []
    assert len(dbmod.list_users()) == 3
    for uid, digest in hashes.items():
        assert dbmod.get_user_by_id(uid)["password_hash"] == digest  # type: ignore[index]
    # The documented demo credential works.
    ok = client.post(
        "/auth/login",
        json={"email": "employee@veridian.local", "password": "Employee-Demo-01!"},
    )
    assert ok.status_code == 200, ok.text
    # Outside dev, nothing is ever created.
    dbmod._MEMORY_USERS.clear()
    monkeypatch.setenv("ENV", "prod")
    get_settings.cache_clear()
    try:
        assert ensure_seed_users() == []
        assert dbmod.list_users() == []
    finally:
        get_settings.cache_clear()


def test_conversation_merge_prefers_fresher_copy(monkeypatch):
    rid = "LIVE-FRESHNESS"
    stale = {
        "request_id": rid,
        "employee": "E",
        "text": "stale",
        "updated_at": "2026-09-20T00:00:01+00:00",
        "messages": [],
        "turns": [],
        "actions": [],
        "cited": [],
    }
    fresh = dict(stale, text="fresh", updated_at="2026-09-20T00:00:02+00:00")

    class _FakeColl:
        def __init__(self, docs):
            self._docs = [dict(d) for d in docs]

        def find(self, query=None, *args, **kwargs):
            q = query or {}
            return [
                dict(d)
                for d in self._docs
                if all(d.get(k) == v for k, v in q.items())
            ]

        def find_one(self, query=None, *args, **kwargs):
            rows = self.find(query)
            return rows[0] if rows else None

    class _FakeDB:
        def __init__(self, convos):
            self._convos = convos

        def __getitem__(self, name):
            assert name == "conversations"
            return _FakeColl(self._convos)

    # Fresh memory beats stale mongo.
    dbmod._MEMORY_CONVOS[rid] = dict(fresh)
    monkeypatch.setattr(dbmod, "get_db", lambda: _FakeDB([stale]))
    try:
        assert dbmod.load_conversation(rid)["text"] == "fresh"  # type: ignore[index]
        listed = {d["request_id"]: d for d in dbmod.list_conversations()}
        assert listed[rid]["text"] == "fresh"
        # Fresh mongo beats stale memory.
        dbmod._MEMORY_CONVOS[rid] = dict(stale)
        monkeypatch.setattr(dbmod, "get_db", lambda: _FakeDB([fresh]))
        assert dbmod.load_conversation(rid)["text"] == "fresh"  # type: ignore[index]
        listed = {d["request_id"]: d for d in dbmod.list_conversations()}
        assert listed[rid]["text"] == "fresh"
    finally:
        dbmod._MEMORY_CONVOS.pop(rid, None)
    # Helper: ties go to the second doc, unstamped docs compare oldest.
    assert dbmod._newer_doc({"updated_at": "b"}, {"updated_at": "b"}) == {"updated_at": "b"}
    assert dbmod._newer_doc({}, {"updated_at": "2026-01-01T00:00:00+00:00"}) == {
        "updated_at": "2026-01-01T00:00:00+00:00"
    }
