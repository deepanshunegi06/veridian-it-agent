"""The HTTP surface.

One decision shapes this file: the agent's work is streamed, not returned. A
request that takes eight seconds and then produces an answer looks like a slow
chatbot. The same eight seconds, with the searches, the clauses found, the
refusals and the corrections arriving as they happen, looks like what it is --
and it is the only way to show that the guarantees are real rather than claimed.

So `/chat` and `/requests/{id}/run` are server-sent event streams, and every
tool call, every refusal and every citation goes down the wire as its own event.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import agent, db, kb, tools
from .auth import ensure_seed_users, get_current_user
from .auth import router as auth_router
from .llm import get_settings
from .tools import Conversation


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    try:
        db.ensure_indexes()
    except Exception:
        pass
    try:
        ensure_seed_users()
    except Exception:
        pass
    yield


def _cors_origins() -> list[str]:
    raw = get_settings().cors_origin_list
    cleaned = [o for o in (r.strip() for r in raw) if o and o != "*"]
    return cleaned or ["http://localhost:3000"]


app = FastAPI(
    title="Veridian IT Desk",
    description="An internal IT service agent that cites what it says and refuses what it cannot decide.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)

# DB-backed session state. Live Conversation objects stay in memory for the
# run (the agent mutates them), while every creation, event and final state
# is persisted via app/db.py (Mongo, with an in-memory fallback when
# MONGODB_URI is empty). Reads fall through to the database so a restart
# does not lose the audit trail.
def _conversation_from_doc(doc: dict) -> Conversation:
    convo = Conversation(
        request_id=doc.get("request_id", ""),
        employee=doc.get("employee", ""),
        text=doc.get("text", ""),
        initial_action=doc.get("initial_action", ""),
        opened=doc.get("opened", ""),
        owner_user_id=doc.get("owner_user_id"),
    )
    convo.turns = list(doc.get("turns", []) or [])
    convo.actions = list(doc.get("actions", []) or [])
    convo.cited = list(doc.get("cited", []) or [])
    convo.messages = list(doc.get("messages", []) or [])
    convo.assigned_to = doc.get("assigned_to")
    convo.assigned_to_user_id = doc.get("assigned_to_user_id")
    convo.ticket_status = doc.get("ticket_status")
    # Migration-safe: old docs lack ticket_* keys and load as None.
    convo.ticket_id = doc.get("ticket_id")
    convo.ticket_category = doc.get("ticket_category")
    convo.ticket_summary = doc.get("ticket_summary")
    convo.ticket_priority = doc.get("ticket_priority")
    # Migration-safe: old docs lack ticket_title and load as None.
    convo.ticket_title = doc.get("ticket_title")
    # Backfill legacy rows that raised via actions before ticket_id existed:
    # the latest raise_ticket action is the thread's ticket.
    if not convo.ticket_id:
        for action in reversed(convo.actions or []):
            if action.get("tool") == "raise_ticket" and action.get("ticket"):
                convo.ticket_id = action.get("ticket")
                convo.ticket_category = convo.ticket_category or action.get("category")
                convo.ticket_summary = convo.ticket_summary or action.get("summary")
                convo.ticket_priority = convo.ticket_priority or action.get("priority")
                break
    convo.best_match = doc.get("best_match", "") or convo.best_match
    convo.followups_asked = int(doc.get("followups_asked", 0) or 0)
    convo.outcome = doc.get("outcome")
    # Migration-safe: old docs lack resolution_source and load as None, which
    # reopens with AI-resolved behaviour (never trapped requiring a human).
    convo.resolution_source = doc.get("resolution_source")
    return convo


class ConversationStore:
    """Dict-like store backed by Mongo (via app/db.py)."""

    def __init__(self) -> None:
        self._cache: dict[str, Conversation] = {}

    def __setitem__(self, key: str, convo: Conversation) -> None:
        self._cache[key] = convo
        try:
            db.save_conversation(convo)
        except Exception:
            pass

    def __getitem__(self, key: str) -> Conversation:
        found = self.get(key)
        if found is None:
            raise KeyError(key)
        return found

    def get(self, key: str, default: Any = None) -> Any:
        if key in self._cache:
            return self._cache[key]
        try:
            doc = db.load_conversation(key)
        except Exception:
            doc = None
        if doc is None:
            return default
        try:
            convo = _conversation_from_doc(doc)
        except Exception:
            return default
        self._cache[key] = convo
        return convo

    def values(self) -> Any:
        try:
            for doc in db.list_conversations():
                rid = doc.get("request_id", "")
                if rid and rid not in self._cache:
                    try:
                        self._cache[rid] = _conversation_from_doc(doc)
                    except Exception:
                        pass
        except Exception:
            pass
        return self._cache.values()

    def items(self) -> Any:
        list(self.values())
        return self._cache.items()

    def __contains__(self, key: object) -> bool:
        if key in self._cache:
            return True
        try:
            return db.load_conversation(str(key)) is not None
        except Exception:
            return False

    def __iter__(self) -> Any:
        list(self.values())
        return iter(self._cache)

    def __len__(self) -> int:
        list(self.values())
        return len(self._cache)

    def clear(self) -> None:
        self._cache.clear()
        try:
            # Keep the global ticket counter so ids stay unique across /reset.
            db.clear_all(clear_counters=False)
        except Exception:
            pass


CONVERSATIONS: ConversationStore = ConversationStore()  # type: ignore[assignment]


# --- reference data -------------------------------------------------------------


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.get("/kb")
def knowledge_base(request: Request) -> list[dict]:
    """Every clause, so the UI can show a citation in full without another call."""
    _me(request)
    overrides = kb.list_overrides()
    return [
        {
            **c.cite(),
            "keywords": c.keywords,
            "conflicts_with": c.conflicts_with,
            "overridden": c.id in overrides,
        }
        for c in kb.effective_clauses().values()
    ]


@app.get("/requests")
def requests(request: Request) -> list[dict]:
    """The employee queue from the data pack, with whatever the agent has done."""
    _me(request)
    out = []
    for row in kb.REQUESTS:
        convo = CONVERSATIONS.get(row["id"])
        out.append(
            {
                **row,
                "opened": str(row["opened"]),
                "outcome": convo.outcome if convo else None,
                "steps": len(convo.actions) if convo else 0,
                "cited": convo.cited if convo else [],
            }
        )
    return out


@app.get("/tickets")
def tickets(request: Request) -> list[dict]:
    """The pre-existing queue, plus anything the agent has raised this session.

    Fixture rows are shared demo data. Raised tickets are scoped: employees
    see only their own threads' tickets, support sees all. Every employee
    thread carries one automatic ticket (POST /threads); an AI ``raise_ticket``
    reuses that id instead of opening a second one, so rows are deduplicated
    by ticket id.
    """
    user = _me(request)
    existing = [{**t, "origin": "data pack"} for t in kb.TICKETS]
    convos = list(CONVERSATIONS.values())
    if not _is_support(user):
        convos = [c for c in convos if (c.owner_user_id or "") == str(user.get("id", ""))]
    for c in convos:
        try:
            _ensure_lazy_auto_ticket(c)
        except Exception:
            pass
    raised_by_id: dict[str, dict] = {}
    for convo in convos:
        tid_field = getattr(convo, "ticket_id", None)
        if isinstance(tid_field, str) and tid_field:
            latest = None
            for a in convo.actions or []:
                if a.get("tool") == "raise_ticket" and a.get("ticket") == tid_field:
                    latest = a
            if latest is not None:
                summary = latest.get("summary") or getattr(
                    convo, "ticket_summary", None
                ) or convo.text
                category = latest.get("category") or getattr(
                    convo, "ticket_category", None
                )
                priority = (
                    latest.get("priority")
                    or getattr(convo, "ticket_priority", None)
                    or "normal"
                )
                origin = "raised by the agent"
            else:
                summary = getattr(convo, "ticket_summary", None) or convo.text
                category = getattr(convo, "ticket_category", None)
                priority = getattr(convo, "ticket_priority", None) or "normal"
                origin = "auto"
            ticket_title = getattr(convo, "ticket_title", None) or summary
            raised_by_id[tid_field] = {
                "id": tid_field,
                "ticket_id": tid_field,
                "ticket": tid_field,
                "employee": convo.employee,
                "summary": summary,
                "status": _ticket_status_text(convo, priority),
                "open": convo.ticket_status != "resolved",
                "origin": origin,
                "request_id": convo.request_id,
                "category": category,
                "assigned_to": convo.assigned_to,
                "ticket_status": convo.ticket_status or "open",
                "outcome": convo.outcome,
                "ticket_title": ticket_title,
                "title": ticket_title,
                "ticket_summary": summary,
            }
        for action in convo.actions or []:
            if action.get("tool") == "raise_ticket" and "ticket" in action:
                tid = str(action["ticket"])
                if tid in raised_by_id:
                    continue
                legacy_summary = action.get("summary")
                legacy_title = getattr(convo, "ticket_title", None) or legacy_summary
                raised_by_id[tid] = {
                    "id": tid,
                    "ticket_id": tid,
                    "ticket": tid,
                    "employee": convo.employee,
                    "summary": legacy_summary,
                    "status": (
                        f"Resolved by {convo.assigned_to}"
                        if convo.ticket_status == "resolved"
                        else f"Assigned to {convo.assigned_to}"
                        if convo.assigned_to
                        else f"Open - {action.get('priority')} priority"
                    ),
                    "open": convo.ticket_status != "resolved",
                    "origin": "raised by the agent",
                    "request_id": convo.request_id,
                    "category": action.get("category"),
                    "assigned_to": convo.assigned_to,
                    "ticket_status": convo.ticket_status or "open",
                    "outcome": convo.outcome,
                    "ticket_title": legacy_title,
                    "title": legacy_title,
                    "ticket_summary": legacy_summary,
                }
    return existing + list(raised_by_id.values())


def _ticket_status_text(convo: Conversation, priority: str | None = None) -> str:
    prio = (
        priority
        or getattr(convo, "ticket_priority", None)
        or _latest_raise_priority(convo)
        or "normal"
    )
    if convo.ticket_status == "resolved":
        return f"Resolved by {convo.assigned_to}" if convo.assigned_to else "Resolved"
    if convo.assigned_to:
        return f"Assigned to {convo.assigned_to}"
    return f"Open - {prio} priority"


def _latest_raise_priority(convo: Conversation) -> str | None:
    for action in reversed(convo.actions or []):
        if action.get("tool") == "raise_ticket" and action.get("priority"):
            return str(action.get("priority"))
    return None


def _thread_summary(convo: Conversation) -> dict:
    last = convo.messages[-1] if convo.messages else None
    ticket_id = getattr(convo, "ticket_id", None)
    ticket_title = getattr(convo, "ticket_title", None)
    return {
        "request_id": convo.request_id,
        "employee": convo.employee,
        "owner_user_id": convo.owner_user_id,
        "text": convo.text,
        "outcome": convo.outcome,
        "resolution_source": getattr(convo, "resolution_source", None),
        "assigned_to": convo.assigned_to,
        "ticket_status": convo.ticket_status,
        "ticket_id": ticket_id,
        "ticket": ticket_id,
        "ticket_category": getattr(convo, "ticket_category", None),
        "ticket_summary": getattr(convo, "ticket_summary", None),
        "ticket_priority": getattr(convo, "ticket_priority", None),
        "ticket_title": ticket_title,
        "title": ticket_title,
        "message_count": len(convo.messages),
        "last_message": last,
        "cited": convo.cited,
    }


def _me(request: Request) -> dict:
    return get_current_user(request)


def _is_support(user: dict) -> bool:
    return str(user.get("role", "")) in ("it_agent", "admin")


def _is_admin(user: dict) -> bool:
    return str(user.get("role", "")) == "admin"


def _require_admin(request: Request) -> dict:
    user = _me(request)
    if not _is_admin(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin only")
    return user


def _require_support(request: Request) -> dict:
    user = _me(request)
    if not _is_support(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Support role required")
    return user


def _can_read(user: dict, convo: Conversation) -> bool:
    if _is_support(user):
        return True
    owner = getattr(convo, "owner_user_id", None)
    return bool(owner) and owner == str(user.get("id", ""))


def _require_readable(request: Request, thread_id: str) -> tuple[dict, Conversation]:
    user = _me(request)
    convo = CONVERSATIONS.get(thread_id)
    if convo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such thread")
    if not _can_read(user, convo):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your thread")
    return user, convo


def _matches_filters(
    convo: Conversation,
    *,
    status_filter: str | None,
    q: str | None,
    assigned: str | None,
) -> bool:
    if status_filter:
        want = status_filter.strip().lower()
        have = {(convo.ticket_status or "").lower(), (convo.outcome or "").lower()}
        if want not in have:
            return False
    if q:
        needle = q.strip().lower()
        hay = f"{convo.request_id} {convo.employee} {convo.text}".lower()
        if needle not in hay:
            return False
    if assigned:
        needle = assigned.strip().lower()
        if needle == "unassigned":
            if convo.assigned_to:
                return False
        elif needle not in (convo.assigned_to or "").lower():
            return False
    return True


@app.get("/threads")
def threads(
    request: Request,
    status: str | None = None,
    q: str | None = None,
    assigned: str | None = None,
) -> list[dict]:
    """Live threads. Employees see only their own; support sees all.

    Optional filters: ``status`` matches ticket_status/outcome, ``q`` searches
    request_id/employee/text, ``assigned`` matches the assigned agent name
    (``unassigned`` for threads nobody joined).
    """
    user = _me(request)
    convos = list(CONVERSATIONS.values())
    if not _is_support(user):
        convos = [c for c in convos if (c.owner_user_id or "") == str(user.get("id", ""))]
    filtered = [c for c in convos if _matches_filters(c, status_filter=status, q=q, assigned=assigned)]
    for c in filtered:
        try:
            _ensure_lazy_auto_ticket(c)
        except Exception:
            pass
    return [_thread_summary(c) for c in filtered]


@app.get("/inbox")
def inbox(
    request: Request,
    status: str | None = None,
    q: str | None = None,
    assigned: str | None = None,
) -> list[dict]:
    """Support queue backed by live conversations (not YAML fixture rows)."""
    _require_support(request)
    filtered = [
        c for c in CONVERSATIONS.values() if _matches_filters(c, status_filter=status, q=q, assigned=assigned)
    ]
    for c in filtered:
        try:
            _ensure_lazy_auto_ticket(c)
        except Exception:
            pass
    return [_thread_summary(c) for c in filtered]


@app.get("/threads/{thread_id}")
def one_thread(thread_id: str, request: Request) -> dict:
    """Full thread: messages, handoff state, audit trail."""
    _, convo = _require_readable(request, thread_id)
    try:
        _ensure_lazy_auto_ticket(convo)
    except Exception:
        pass
    return {
        **_thread_summary(convo),
        "messages": convo.messages,
        "turns": convo.turns,
        "actions": convo.actions,
    }


class ThreadCreate(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    # Accepted for backwards compatibility, but ignored: the employee name and
    # owner are always derived from auth, never trusted from the body.
    employee: str = Field(default="You", max_length=80)


@app.post("/threads")
def create_thread(body: ThreadCreate, request: Request) -> dict:
    """Start a user thread. Posting the first message runs the agent (SSE).

    Exactly one automatic ticket is attached here, before any AI run, using
    the global counter. Creation never sets ``outcome`` (a fresh ticket is
    open, never escalated) and never logs an audit action so first-message
    deduplication stays intact.
    """
    user = _me(request)
    text = body.message.strip()
    if not text:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Message must not be blank")
    request_id = f"LIVE-{uuid.uuid4().hex[:8].upper()}"
    while request_id in CONVERSATIONS:
        request_id = f"LIVE-{uuid.uuid4().hex[:8].upper()}"
    convo = Conversation(
        request_id=request_id,
        employee=str(user.get("name", "You")),
        text=body.message,
        owner_user_id=str(user.get("id", "")),
    )
    tools.ensure_auto_ticket(convo)
    CONVERSATIONS[request_id] = convo
    return {
        "request_id": request_id,
        "messages": convo.messages,
        "ticket_id": convo.ticket_id,
        "ticket": convo.ticket_id,
        "ticket_status": convo.ticket_status,
        "ticket_summary": convo.ticket_summary,
        "ticket_category": convo.ticket_category,
        "ticket_priority": convo.ticket_priority,
        "ticket_title": getattr(convo, "ticket_title", None),
        "title": getattr(convo, "ticket_title", None),
    }


class ThreadMessage(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


def _is_first_message_repost(convo: Conversation, text: str) -> bool:
    """True when the client re-sends the creation message to trigger the run.

    The product flow creates the thread (which already stores the first user
    message) and then POSTs the same text to ``/messages`` to run the agent.
    Appending it again would store two identical user bubbles, inflate
    ``message_count`` and feed the agent a duplicated context, so the re-post
    only triggers the run. Deliberately narrow: any agent activity, extra
    messages, or differing text means a genuine follow-up, stored normally.
    """
    if text != (convo.text or ""):
        return False
    if convo.turns or convo.actions:
        return False
    msgs = convo.messages or []
    if len(msgs) != 1:
        return False
    first = msgs[0]
    return first.get("speaker") == "user" and first.get("text") == text


def _is_handed_off(convo: Conversation) -> bool:
    """True once a human owns the thread: AI-escalated or IT-joined.

    After this point employee follow-ups are stored and streamed to the live
    thread, but must never trigger ``agent.run`` again -- otherwise the
    employee sees an AI greeting while a human is already replying.
    """
    if getattr(convo, "outcome", None) == "escalated":
        return True
    if getattr(convo, "assigned_to", None):
        return True
    if getattr(convo, "assigned_to_user_id", None):
        return True
    return False


@app.post("/threads/{thread_id}/messages")
async def post_message(thread_id: str, body: ThreadMessage, request: Request) -> StreamingResponse:
    """Append a follow-up user message and run the agent over the thread (SSE).

    Only the thread owner may post here; support replies go through
    ``/human`` so a support session can never inject ``user``-speaker
    messages or trigger agent runs on someone else's thread.

    Once the thread is handed off (escalated outcome or IT join), the message
    is stored and fanned out to the live thread stream, but the AI is not
    run again. The SSE response still finishes with start/handoff/outcome/done
    so the frontend clears its running state, without any ``say`` AI reply.

    A resolved thread reopens on a new employee message instead of 409:
    AI-resolved (or legacy threads missing ``resolution_source``) reopen for
    the AI via the normal stream; human-resolved threads reopen into a human
    handoff state via the quiet handoff path with the old assignment cleared.
    """

    def _title_fallback(c: Conversation) -> None:
        try:
            existing_title = getattr(c, "ticket_title", None)
            if not (isinstance(existing_title, str) and existing_title.strip()):
                from .agent import _fallback_title

                fallback = _fallback_title(c.text or "")
                c.ticket_title = fallback
                try:
                    actions = getattr(c, "actions", None) or []
                    has_raise = any(
                        isinstance(a, dict)
                        and a.get("tool") == "raise_ticket"
                        and "ticket" in a
                        for a in actions
                    )
                except Exception:
                    has_raise = False
                if not has_raise or not getattr(c, "ticket_summary", None):
                    c.ticket_summary = fallback
        except Exception:
            pass

    user, convo = _require_readable(request, thread_id)
    if convo.owner_user_id and convo.owner_user_id != str(user.get("id", "")):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Only the thread owner can send messages here"
        )
    if not body.message.strip():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Message must not be blank"
        )
    if convo.outcome == "resolved":
        # Migration-safe: old docs lack resolution_source and default to None,
        # which reopens with AI behaviour so they are never trapped.
        src = getattr(convo, "resolution_source", None)
        if src == "human":
            # Human-resolved: accept the message, reopen into a human handoff
            # (no AI run, no AI say) and clear the old assignment so the
            # thread returns to the IT queue until someone joins again.
            convo.say("user", body.message, name=str(user.get("name", convo.employee)))
            convo.text = body.message
            try:
                _ensure_lazy_auto_ticket(convo)
            except Exception:
                pass
            _title_fallback(convo)
            convo.outcome = "escalated"
            convo.ticket_status = "open"
            convo.assigned_to = None
            convo.assigned_to_user_id = None
            convo.resolution_source = None
            _persist_convo(convo)
            return StreamingResponse(
                _stream_handoff(convo), media_type="text/event-stream", headers=SSE_HEADERS
            )
        # AI-resolved (or legacy None): reopen for the AI. Must not require
        # human intervention just because the thread was previously resolved.
        is_repost = _is_first_message_repost(convo, body.message)
        if not is_repost:
            convo.say("user", body.message, name=str(user.get("name", convo.employee)))
            convo.text = body.message
        try:
            _ensure_lazy_auto_ticket(convo)
        except Exception:
            pass
        convo.outcome = None
        convo.ticket_status = "open"
        convo.resolution_source = None
        _persist_convo(convo)
        return StreamingResponse(_stream(convo), media_type="text/event-stream", headers=SSE_HEADERS)
    is_repost = _is_first_message_repost(convo, body.message)
    if not is_repost:
        convo.say("user", body.message, name=str(user.get("name", convo.employee)))
        convo.text = body.message
    # Lazily attach the automatic ticket for threads that predate the field
    # (exactly once; no-op when one already exists). Never touches outcome.
    # Shared helper also reuses a legacy raise_ticket action id when present
    # so no duplicate ticket is allocated, and skips REQ-* fixture rows.
    try:
        _ensure_lazy_auto_ticket(convo)
    except Exception:
        pass
    if _is_handed_off(convo):
        # Human owns the thread: persist the employee message (and a
        # deterministic title fallback when missing, without invoking any
        # model) and return a clean handoff stream with no AI `say`.
        try:
            existing_title = getattr(convo, "ticket_title", None)
            if not (isinstance(existing_title, str) and existing_title.strip()):
                from .agent import _fallback_title

                fallback = _fallback_title(convo.text or "")
                convo.ticket_title = fallback
                try:
                    actions = getattr(convo, "actions", None) or []
                    has_raise = any(
                        isinstance(a, dict)
                        and a.get("tool") == "raise_ticket"
                        and "ticket" in a
                        for a in actions
                    )
                except Exception:
                    has_raise = False
                if not has_raise or not getattr(convo, "ticket_summary", None):
                    convo.ticket_summary = fallback
        except Exception:
            pass
        _persist_convo(convo)
        return StreamingResponse(
            _stream_handoff(convo), media_type="text/event-stream", headers=SSE_HEADERS
        )
    if not is_repost:
        # A follow-up answer reopens a paused run. Without this, `is_closed()`
        # stays true (`waiting_on_employee`) and the next `agent.run` returns
        # immediately after its first tool without deciding anything.
        # Handed-off threads return above, so waiting reopen still works when
        # no human has taken over.
        if convo.outcome == "waiting_on_employee":
            convo.outcome = None
    _persist_convo(convo)
    return StreamingResponse(_stream(convo), media_type="text/event-stream", headers=SSE_HEADERS)


@app.get("/threads/{thread_id}/stream")
async def thread_stream(thread_id: str, request: Request) -> StreamingResponse:
    """Live message feed for a thread (user realtime + admin watch).

    Polls the persisted thread and emits each new message as its own event.
    Ends when the client disconnects.
    """
    _require_readable(request, thread_id)

    async def gen() -> AsyncIterator[str]:
        yield ": connected\n\n"
        last = -1
        ticks = 0
        while True:
            convo = CONVERSATIONS.get(thread_id)
            if convo is None:
                yield _event("error", {"detail": "No such thread"})
                return
            for msg in convo.messages[last + 1 :]:
                last += 1
                yield _event("message", msg)
            ticks += 1
            if ticks % 15 == 0:
                yield ": ping\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


class JoinBody(BaseModel):
    # Accepted for backwards compatibility, but ignored: the agent identity
    # always comes from auth.
    agent_name: str = Field(default="", max_length=80)


def _require_thread(thread_id: str) -> Conversation:
    convo = CONVERSATIONS.get(thread_id)
    if convo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such thread")
    return convo


@app.post("/threads/{thread_id}/join")
def join_thread(thread_id: str, body: JoinBody, request: Request) -> dict:
    """IT support joins: records who, flips ticket to assigned, announces it.

    Joining a resolved thread is refused, re-joining by the same agent is an
    idempotent no-op (no duplicate announcement), and taking over a thread
    another agent already joined requires an admin.
    """
    user = _require_support(request)
    convo = _require_thread(thread_id)
    if convo.outcome == "resolved" or convo.ticket_status == "resolved":
        raise HTTPException(status.HTTP_409_CONFLICT, "Thread is resolved")
    me_id = str(user.get("id", ""))
    name = str(user.get("name", "")).strip() or "Support"
    stored_name = (convo.assigned_to or "").strip().lower()
    if convo.assigned_to and (
        convo.assigned_to_user_id == me_id
        # Legacy rows predate assigned_to_user_id: match by name so a re-join
        # stays idempotent instead of announcing twice.
        or (
            not convo.assigned_to_user_id
            and stored_name
            and stored_name == name.lower()
        )
    ):
        return _thread_summary(convo)
    if convo.assigned_to and convo.assigned_to_user_id and not _is_admin(user):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Already joined by another agent"
        )
    convo.assigned_to = name
    convo.assigned_to_user_id = me_id
    if convo.ticket_status in (None, "open"):
        convo.ticket_status = "assigned"
    convo.say("system", f"{name} has joined the chat to resolve the issue.")
    _persist_convo(convo)
    try:
        db.log_event(thread_id, {"type": "join", "agent": name})
    except Exception:
        pass
    return _thread_summary(convo)


class HumanBody(BaseModel):
    # Accepted for backwards compatibility, but ignored: identity from auth.
    agent_name: str = Field(default="", max_length=80)
    message: str = Field(min_length=1, max_length=2000)


@app.post("/threads/{thread_id}/human")
def human_message(thread_id: str, body: HumanBody, request: Request) -> dict:
    """A joined support agent replies. Human words are logged, never policy-checked.

    Only the assigned agent (or an admin) may reply, so assignment stays
    meaningful; anyone else must join (or be an admin) first.
    """
    user = _require_support(request)
    convo = _require_thread(thread_id)
    if convo.outcome == "resolved" or convo.ticket_status == "resolved":
        raise HTTPException(status.HTTP_409_CONFLICT, "Thread is resolved")
    if not convo.assigned_to:
        raise HTTPException(status.HTTP_409_CONFLICT, "Nobody has joined this thread yet")
    assigned_id = getattr(convo, "assigned_to_user_id", None)
    if assigned_id and assigned_id != str(user.get("id", "")) and not _is_admin(user):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Only the assigned agent or an admin can reply here",
        )
    name = str(user.get("name", "")).strip() or "Support"
    msg = convo.say("human", body.message, name=name)
    _persist_convo(convo)
    return {"message": msg, "thread": _thread_summary(convo)}


@app.post("/threads/{thread_id}/resolve")
def resolve_thread(thread_id: str, body: JoinBody, request: Request) -> dict:
    """A joined support agent closes the thread.

    Resolving an already-resolved thread is an idempotent no-op (no duplicate
    announcement) so retries and double-clicks stay safe.
    """
    user = _require_support(request)
    convo = _require_thread(thread_id)
    if convo.outcome == "resolved" or convo.ticket_status == "resolved":
        return _thread_summary(convo)
    if not convo.assigned_to:
        raise HTTPException(status.HTTP_409_CONFLICT, "Nobody has joined this thread yet")
    assigned_id = getattr(convo, "assigned_to_user_id", None)
    if assigned_id and assigned_id != str(user.get("id", "")) and not _is_admin(user):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Only the assigned agent or an admin can resolve this thread",
        )
    name = str(user.get("name", "")).strip() or "Support"
    convo.outcome = "resolved"
    convo.resolution_source = "human"
    convo.ticket_status = "resolved"
    convo.say("system", f"{name} marked this issue as resolved.")
    _persist_convo(convo)
    return _thread_summary(convo)


# --- knowledge base admin -------------------------------------------------------


@app.get("/kb/overrides")
def kb_overrides(request: Request) -> dict:
    _require_admin(request)
    return kb.list_overrides()


class ClauseEdit(BaseModel):
    title: str | None = None
    text: str | None = None
    authority: str | None = None
    keywords: list[str] | None = None
    conflicts_with: list[str] | None = None


@app.put("/kb/{clause_id}")
def edit_clause(clause_id: str, body: ClauseEdit, request: Request) -> dict:
    """Edit a clause (admin override; base YAML untouched)."""
    _require_admin(request)
    if kb.CLAUSES.get(clause_id) is None and clause_id not in kb.list_overrides():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such clause")
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    if not patch:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Nothing to update")
    if "conflicts_with" in patch:
        # Reciprocate so a conflict can never be declared only one way.
        for other in patch["conflicts_with"]:
            other_eff = kb.effective_clauses().get(other)
            if other_eff is None:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY, f"No such clause: {other}"
                )
            if clause_id not in other_eff.conflicts_with:
                kb.save_override(
                    other, {"conflicts_with": [*other_eff.conflicts_with, clause_id]}
                )
    kb.save_override(clause_id, patch)
    saved = kb.effective_clauses().get(clause_id)
    return saved.cite() if saved else {"id": clause_id}


class ClauseCreate(BaseModel):
    id: str = Field(pattern=r"^(KB-\d{2}|ASSET-\d{2})$")
    title: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=1, max_length=2000)
    authority: str = Field(min_length=1, max_length=80)
    keywords: list[str] = Field(default_factory=list)
    conflicts_with: list[str] = Field(default_factory=list)


@app.post("/kb")
def create_clause(body: ClauseCreate, request: Request) -> dict:
    """Add a clause. Id must be fresh; conflicts reciprocate automatically."""
    _require_admin(request)
    if kb.effective_clauses().get(body.id) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Clause id already exists")
    for other in body.conflicts_with:
        if kb.effective_clauses().get(other) is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, f"No such clause: {other}"
            )
    kb.save_override(body.id, body.model_dump())
    for other in body.conflicts_with:
        other_eff = kb.effective_clauses().get(other)
        assert other_eff is not None
        if body.id not in other_eff.conflicts_with:
            kb.save_override(other, {"conflicts_with": [*other_eff.conflicts_with, body.id]})
    saved = kb.effective_clauses().get(body.id)
    assert saved is not None
    return saved.cite()


@app.get("/requests/{request_id}")
def one_request(request_id: str, request: Request) -> dict:
    """Everything that happened to one request. This is the audit trail.

    Fixture rows are shared demo data, but a live conversation with an owner
    is that owner's: employees (non-support) get 403 for anyone else's
    thread, so this route cannot bypass the /threads/{id} ownership check.
    """
    user = _me(request)
    row = next((r for r in kb.REQUESTS if r["id"] == request_id), None)
    convo = CONVERSATIONS.get(request_id)
    if row is None and convo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such request")
    if convo is not None and getattr(convo, "owner_user_id", None):
        if not _is_support(user) and convo.owner_user_id != str(user.get("id", "")):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your thread")
    if convo is not None:
        try:
            _ensure_lazy_auto_ticket(convo)
        except Exception:
            pass
    return {
        "id": request_id,
        "employee": convo.employee if convo else row["employee"],
        "text": convo.text if convo else row["text"],
        "opened": str(row["opened"]) if row else "",
        "initial_action": row.get("initial_action", "") if row else "",
        "outcome": convo.outcome if convo else None,
        "resolution_source": getattr(convo, "resolution_source", None) if convo else None,
        "turns": convo.turns if convo else [],
        "actions": convo.actions if convo else [],
        "cited": convo.cited if convo else [],
        "messages": convo.messages if convo else [],
        "assigned_to": convo.assigned_to if convo else None,
        "ticket_status": convo.ticket_status if convo else None,
        "ticket_id": getattr(convo, "ticket_id", None) if convo else None,
        "ticket": getattr(convo, "ticket_id", None) if convo else None,
        "ticket_summary": getattr(convo, "ticket_summary", None) if convo else None,
        "ticket_category": getattr(convo, "ticket_category", None) if convo else None,
        "ticket_priority": getattr(convo, "ticket_priority", None) if convo else None,
        "ticket_title": getattr(convo, "ticket_title", None) if convo else None,
        "title": getattr(convo, "ticket_title", None) if convo else None,
    }


# --- running the agent ----------------------------------------------------------


def _event(name: str, data: Any) -> str:
    return f"event: {name}\ndata: {json.dumps(data, default=str)}\n\n"


def _persist_event(request_id: str, event_type: str, payload: dict) -> None:
    try:
        db.log_event(request_id, {"type": event_type, **payload})
    except Exception:
        pass


def _persist_convo(convo: Conversation) -> None:
    try:
        db.save_conversation(convo)
    except Exception:
        pass


def _is_lazy_ticket_eligible(convo: Conversation) -> bool:
    """Only employee-owned LIVE threads qualify for lazy auto-ticketing.

    Legacy data-pack rows (REQ-*) are shared demo data and must never be
    auto-ticketed en masse; only LIVE-* threads created via POST /threads
    (which always sets owner_user_id from auth) are eligible.
    """
    rid = str(getattr(convo, "request_id", "") or "")
    if not rid.startswith("LIVE-"):
        return False
    owner = getattr(convo, "owner_user_id", None)
    return bool(owner and str(owner).strip())


def _ensure_lazy_auto_ticket(convo: Conversation) -> bool:
    """Attach exactly one automatic ticket on a safe read/hydration path.

    Returns True when the conversation was mutated (and persisted).
    Idempotent: a present ticket_id is never replaced and the global
    counter is never consumed twice. Legacy raise_ticket actions are
    reused without allocating a new id. ``outcome``/escalation state is
    never altered; only ticket_* fields (and ticket_status None -> open)
    may be set.
    """
    existing = getattr(convo, "ticket_id", None)
    if isinstance(existing, str) and existing:
        return False
    # Defensive backfill: a thread that raised via the AI before ticket_id
    # existed may carry the id only in actions (e.g. cached objects that
    # bypassed _conversation_from_doc). Reuse it instead of allocating.
    try:
        actions = list(getattr(convo, "actions", None) or [])
    except Exception:
        actions = []
    for action in reversed(actions):
        try:
            if action.get("tool") == "raise_ticket" and action.get("ticket"):
                convo.ticket_id = str(action.get("ticket"))
                if not getattr(convo, "ticket_category", None) and action.get("category"):
                    convo.ticket_category = action.get("category")
                if not getattr(convo, "ticket_summary", None) and action.get("summary"):
                    convo.ticket_summary = action.get("summary")
                if not getattr(convo, "ticket_priority", None) and action.get("priority"):
                    convo.ticket_priority = action.get("priority")
                if getattr(convo, "ticket_status", None) is None:
                    convo.ticket_status = "open"
                _persist_convo(convo)
                return True
        except Exception:
            continue
    if not _is_lazy_ticket_eligible(convo):
        return False
    try:
        tools.ensure_auto_ticket(convo)
    except Exception:
        return False
    _persist_convo(convo)
    return True


async def _stream(convo: Conversation) -> AsyncIterator[str]:
    """Run the agent, emitting each step as it happens.

    The loop is synchronous and blocking, so it runs on a worker thread while
    this coroutine drains a queue the thread writes into. That is what lets the
    browser see a refusal at the moment it happens rather than in a summary
    afterwards.
    """
    queue: asyncio.Queue[dict | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def emit(event: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, event)

    def work() -> None:
        try:
            agent.run(convo, on_event=emit)
        except Exception as exc:  # the browser deserves to know rather than hang
            emit({"type": "error", "detail": f"{type(exc).__name__}: {exc}"[:300]})
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    task = asyncio.create_task(asyncio.to_thread(work))
    _persist_event(convo.request_id, "start", {"employee": convo.employee})
    yield _event(
        "start",
        {
            "request_id": convo.request_id,
            "employee": convo.employee,
            "ticket_id": getattr(convo, "ticket_id", None),
            "ticket": getattr(convo, "ticket_id", None),
            "ticket_title": getattr(convo, "ticket_title", None),
            "title": getattr(convo, "ticket_title", None),
        },
    )

    while True:
        event = await queue.get()
        if event is None:
            break
        event_type = event.pop("type")
        # Persist every step, including `refused` (previously stream-only).
        _persist_event(convo.request_id, event_type, dict(event))
        yield _event(event_type, event)

    await task
    done_payload = {
        "outcome": convo.outcome,
        "cited": convo.cited,
        "steps": len(convo.actions),
        "turns": convo.turns,
        "ticket_id": getattr(convo, "ticket_id", None),
        "ticket": getattr(convo, "ticket_id", None),
        "ticket_status": convo.ticket_status,
        "ticket_title": getattr(convo, "ticket_title", None),
        "title": getattr(convo, "ticket_title", None),
        "ticket_summary": getattr(convo, "ticket_summary", None),
    }
    _persist_event(convo.request_id, "done", dict(done_payload))
    _persist_convo(convo)
    yield _event("done", done_payload)


async def _stream_handoff(convo: Conversation) -> AsyncIterator[str]:
    """Clean SSE finish for handed-off threads without running the AI.

    The employee message was already appended by ``post_message``. Emit start,
    handoff, outcome and done so the frontend clears its running state. Never
    emits ``say`` and never calls ``agent.run``/any model.
    """
    ticket_id = getattr(convo, "ticket_id", None)
    title = getattr(convo, "ticket_title", None) or getattr(convo, "ticket_summary", None)
    _persist_event(convo.request_id, "start", {"employee": convo.employee, "handoff": True})
    yield _event(
        "start",
        {
            "request_id": convo.request_id,
            "employee": convo.employee,
            "ticket_id": ticket_id,
            "ticket": ticket_id,
            "ticket_title": title,
            "title": title,
            "handoff": True,
        },
    )
    handoff_payload = {
        "outcome": convo.outcome,
        "assigned_to": convo.assigned_to,
        "ticket_id": ticket_id,
        "ticket": ticket_id,
        "ticket_status": convo.ticket_status,
        "ticket_title": title,
        "title": title,
        "handoff": True,
    }
    _persist_event(convo.request_id, "handoff", dict(handoff_payload))
    yield _event("handoff", handoff_payload)
    _persist_event(convo.request_id, "outcome", {"outcome": convo.outcome})
    yield _event("outcome", {"outcome": convo.outcome})
    done_payload = {
        "outcome": convo.outcome,
        "cited": convo.cited,
        "steps": len(convo.actions),
        "turns": convo.turns,
        "ticket_id": ticket_id,
        "ticket": ticket_id,
        "ticket_status": convo.ticket_status,
        "ticket_title": title,
        "title": title,
        "handoff": True,
    }
    _persist_event(convo.request_id, "done", dict(done_payload))
    _persist_convo(convo)
    yield _event("done", done_payload)


SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    # Nginx and friends buffer by default, which turns a live trace back into a
    # single late blob. This is the header that stops them.
    "X-Accel-Buffering": "no",
}


@app.post("/requests/{request_id}/run")
async def run_request(request_id: str, request: Request) -> StreamingResponse:
    """Work one request from the data pack. Admin-only legacy demo route."""
    admin = _require_admin(request)
    row = next((r for r in kb.REQUESTS if r["id"] == request_id), None)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such request")

    convo = Conversation(
        request_id=row["id"],
        employee=row["employee"],
        text=row["text"],
        initial_action=row.get("initial_action", ""),
        opened=str(row["opened"]),
        owner_user_id=str(admin.get("id", "")),
    )
    CONVERSATIONS[request_id] = convo
    return StreamingResponse(_stream(convo), media_type="text/event-stream", headers=SSE_HEADERS)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    # Accepted for backwards compatibility, but ignored: identity from auth.
    employee: str = Field(default="You", max_length=80)


@app.post("/chat")
async def chat(body: ChatRequest, request: Request) -> StreamingResponse:
    """Anything a visitor types. Same agent, same tools, same refusals.

    Admin-only legacy demo route; identity comes from auth.
    """
    admin = _require_admin(request)
    # uuid-short, not len()+1: the old counter collided after /reset and
    # under concurrent chats (two chats could read the same length).
    request_id = f"LIVE-{uuid.uuid4().hex[:8].upper()}"
    while request_id in CONVERSATIONS:
        request_id = f"LIVE-{uuid.uuid4().hex[:8].upper()}"
    convo = Conversation(
        request_id=request_id,
        employee=str(admin.get("name", "You")),
        text=body.message,
        owner_user_id=str(admin.get("id", "")),
    )
    CONVERSATIONS[convo.request_id] = convo
    return StreamingResponse(_stream(convo), media_type="text/event-stream", headers=SSE_HEADERS)


@app.get("/stats")
def stats(request: Request) -> dict:
    """What the agent has done, for the board at the top of the queue.

    Scoped like /threads: employees see counts over their own threads only,
    support sees everything. Otherwise any signed-in user could enumerate
    how much activity everyone else generates.
    """
    user = _me(request)
    done = list(CONVERSATIONS.values())
    if not _is_support(user):
        done = [c for c in done if (c.owner_user_id or "") == str(user.get("id", ""))]
    # Refusals are now logged into actions (see tools._refuse) so this scan
    # finds them; previously it was always 0. Count any tool's refusal, not
    # just resolve, and take the max with the persisted event stream so a
    # restart does not lose the count.
    actions_refusals = sum(1 for c in done for a in c.actions if "refused" in a)
    try:
        events_refusals = db.count_refusals()
    except Exception:
        events_refusals = 0
    return {
        "handled": len(done),
        "resolved": sum(1 for c in done if c.outcome == "resolved"),
        "escalated": sum(1 for c in done if c.outcome == "escalated"),
        "tickets": sum(1 for c in done if c.outcome == "ticket_raised"),
        "waiting": sum(1 for c in done if c.outcome == "waiting_on_employee"),
        "refusals": max(actions_refusals, events_refusals),
        "total_requests": len(kb.REQUESTS),
    }


@app.post("/reset")
def reset(request: Request) -> dict:
    """Clear the session. Handy mid-demo, and the data pack is untouched.

    Admin-only: clearing live threads is destructive.
    """
    _require_admin(request)
    CONVERSATIONS.clear()
    return {"cleared": True}


# Keep the tools module imported for its side-effect-free registry, and make the
# dependency explicit so a linter does not helpfully remove it.
assert tools.TOOLS
