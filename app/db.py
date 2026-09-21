"""MongoDB persistence with an in-memory fallback.

Mirrors the singleton pattern from ``D:\\morbin\\lib\\db.ts`` (one shared
client, explicit index setup) but in Python with sync pymongo.

If ``MONGODB_URI`` is empty (tests, local runs without a database) every
function falls back to module-level in-memory dicts/lists, so
``pytest tests -q`` passes with no network and no key. No credentials are
hardcoded; configuration comes from the environment only.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any


def _uri() -> str:
    # An explicit process environment value wins. This is important for tests
    # and deployments that intentionally set MONGODB_URI="" to force the
    # memory fallback. Normal Uvicorn launches usually do not export .env
    # values into os.environ, so fall back to the shared pydantic settings
    # loader used by the rest of the application.
    if "MONGODB_URI" in os.environ:
        return os.environ.get("MONGODB_URI", "").strip()
    try:
        from .llm import get_settings

        return str(get_settings().mongodb_uri).strip()
    except Exception:
        return ""


def _db_name() -> str:
    if "VERIDIAN_DB" in os.environ:
        return os.environ.get("VERIDIAN_DB", "").strip() or "veridian_it_agent"
    try:
        from .llm import get_settings

        return str(get_settings().veridian_db).strip() or "veridian_it_agent"
    except Exception:
        return "veridian_it_agent"


# --- in-memory fallback (no URI) -------------------------------------------

_MEMORY_CONVOS: dict[str, dict] = {}
_MEMORY_EVENTS: list[dict] = []
_MEMORY_COUNTERS: dict[str, int] = {"ticket_seq": 0}
_MEMORY_USERS: dict[str, dict] = {}

# --- mongo singleton --------------------------------------------------------

_CLIENT: Any = None
_CLIENT_URI: str | None = None


def _get_pymongo_client_class() -> Any | None:
    try:
        from pymongo import MongoClient  # type: ignore[import-not-found]

        return MongoClient
    except Exception:
        return None


def is_mongo_enabled() -> bool:
    """True when a URI is configured and pymongo is importable."""
    return bool(_uri()) and _get_pymongo_client_class() is not None


def get_client() -> Any | None:
    """Shared MongoClient, or None when falling back to memory."""
    global _CLIENT, _CLIENT_URI
    uri = _uri()
    if not uri:
        return None
    MongoClient = _get_pymongo_client_class()
    if MongoClient is None:
        return None
    if _CLIENT is None or _CLIENT_URI != uri:
        try:
            _CLIENT = MongoClient(uri, serverSelectionTimeoutMS=3000)
        except Exception:
            return None
        _CLIENT_URI = uri
    return _CLIENT


def get_db() -> Any | None:
    """Return the database handle, or None when using the in-memory fallback."""
    client = get_client()
    if client is None:
        return None
    try:
        return client[_db_name()]
    except Exception:
        return None


def ensure_indexes() -> None:
    """Create indexes. No-op without Mongo. Never raises."""
    db = get_db()
    if db is None:
        return
    try:
        db["conversations"].create_index("request_id", unique=True)
        db["events"].create_index([("request_id", 1), ("at", 1)])
        db["events"].create_index("type")
        db["users"].create_index("email", unique=True)
        db["users"].create_index("id", unique=True)
    except Exception:
        pass


# --- conversations ----------------------------------------------------------


def _convo_to_doc(convo: Any) -> dict:
    if isinstance(convo, dict):
        doc = dict(convo)
    else:
        opened_at = getattr(convo, "opened_at", None)
        if isinstance(opened_at, datetime):
            opened_at_s = opened_at.isoformat()
        else:
            opened_at_s = str(opened_at or "")
        doc = {
            "request_id": getattr(convo, "request_id", ""),
            "employee": getattr(convo, "employee", ""),
            "owner_user_id": getattr(convo, "owner_user_id", None),
            "text": getattr(convo, "text", ""),
            "initial_action": getattr(convo, "initial_action", ""),
            "opened": getattr(convo, "opened", ""),
            "turns": list(getattr(convo, "turns", []) or []),
            "actions": list(getattr(convo, "actions", []) or []),
            "cited": list(getattr(convo, "cited", []) or []),
            "messages": list(getattr(convo, "messages", []) or []),
            "assigned_to": getattr(convo, "assigned_to", None),
            "assigned_to_user_id": getattr(convo, "assigned_to_user_id", None),
            "ticket_status": getattr(convo, "ticket_status", None),
            "ticket_id": getattr(convo, "ticket_id", None),
            "ticket_category": getattr(convo, "ticket_category", None),
            "ticket_summary": getattr(convo, "ticket_summary", None),
            "ticket_priority": getattr(convo, "ticket_priority", None),
            "ticket_title": getattr(convo, "ticket_title", None),
            "best_match": getattr(convo, "best_match", ""),
            "followups_asked": getattr(convo, "followups_asked", 0),
            "outcome": getattr(convo, "outcome", None),
            "resolution_source": getattr(convo, "resolution_source", None),
            "opened_at": opened_at_s,
        }
    doc["request_id"] = doc.get("request_id", "")
    doc["updated_at"] = datetime.now(UTC).isoformat()
    return doc


def save_conversation(convo: Any) -> None:
    """Upsert a conversation. Falls back to memory without Mongo."""
    doc = _convo_to_doc(convo)
    rid = doc.get("request_id", "")
    if not rid:
        return
    db = get_db()
    if db is None:
        _MEMORY_CONVOS[rid] = doc
        return
    try:
        db["conversations"].update_one({"request_id": rid}, {"$set": doc}, upsert=True)
    except Exception:
        _MEMORY_CONVOS[rid] = doc


def load_conversation(request_id: str) -> dict | None:
    """Load one conversation doc (without _id), or None.

    When both stores hold a copy (e.g. memory saved while mongo was
    unreachable), the fresher ``updated_at`` wins instead of mongo by
    default, so a restart or outage cannot roll the audit trail backwards.
    """
    mem = _MEMORY_CONVOS.get(request_id)
    mem_doc = dict(mem) if mem is not None else None
    db = get_db()
    if db is None:
        return mem_doc
    try:
        doc = db["conversations"].find_one({"request_id": request_id}, {"_id": 0})
    except Exception:
        return mem_doc
    if doc is None:
        # Fall back to memory copy (e.g. saved while mongo was unreachable).
        return mem_doc
    mongo_doc = dict(doc)
    if mem_doc is not None:
        return _newer_doc(mem_doc, mongo_doc)
    return mongo_doc


def list_conversations() -> list[dict]:
    """All persisted conversation docs, memory + mongo merged by request_id.

    Either store can hold the fresher copy (e.g. memory saved while mongo
    was unreachable), so the winner is picked by ``updated_at``, not by
    which store answered.
    """
    merged: dict[str, dict] = {k: dict(v) for k, v in _MEMORY_CONVOS.items()}
    db = get_db()
    if db is not None:
        try:
            for doc in db["conversations"].find({}, {"_id": 0}):
                rid = doc.get("request_id", "")
                if not rid:
                    continue
                fresh = dict(doc)
                prev = merged.get(rid)
                merged[rid] = _newer_doc(prev, fresh) if prev is not None else fresh
        except Exception:
            pass
    return list(merged.values())


def _doc_updated_at(doc: dict) -> str:
    """Sortable freshness marker for a persisted doc.

    All writers stamp ``updated_at`` as ``datetime.now(UTC).isoformat()`` in
    the same shape, so a plain string comparison orders them. Docs that
    predate the stamp compare as oldest.
    """
    try:
        return str(doc.get("updated_at", "") or "")
    except Exception:
        return ""


def _newer_doc(first: dict, second: dict) -> dict:
    """Return whichever doc carries the fresher ``updated_at`` (ties: second)."""
    if _doc_updated_at(first) > _doc_updated_at(second):
        return first
    return second


# --- events (every SSE event, including refused) ----------------------------


def log_event(request_id: str, event: dict) -> None:
    """Persist one SSE event. Refusals are stored like any other event."""
    record = {"request_id": request_id, "at": datetime.now(UTC).isoformat(), **dict(event)}
    db = get_db()
    if db is None:
        _MEMORY_EVENTS.append(record)
        return
    try:
        db["events"].insert_one(dict(record))
    except Exception:
        _MEMORY_EVENTS.append(record)


def list_events(request_id: str | None = None) -> list[dict]:
    """All events, optionally filtered. Memory + mongo combined."""
    out = [e for e in _MEMORY_EVENTS if request_id is None or e.get("request_id") == request_id]
    db = get_db()
    if db is not None:
        try:
            query: dict = {} if request_id is None else {"request_id": request_id}
            for doc in db["events"].find(query, {"_id": 0}):
                out.append(dict(doc))
        except Exception:
            pass
    return out


def count_refusals(request_id: str | None = None) -> int:
    """Number of persisted ``refused`` events."""
    return sum(1 for e in list_events(request_id) if e.get("type") == "refused")


# --- global ticket counter ---------------------------------------------------

_TICKET_BASE = 1051  # first seq (1) -> TK-1052


def next_ticket_id() -> str:
    """Globally unique ticket id. Mongo counter, in-memory fallback otherwise."""
    db = get_db()
    if db is None:
        _MEMORY_COUNTERS["ticket_seq"] = _MEMORY_COUNTERS.get("ticket_seq", 0) + 1
        return f"TK-{_TICKET_BASE + _MEMORY_COUNTERS['ticket_seq']}"
    try:
        from pymongo import ReturnDocument  # type: ignore[import-not-found]

        doc = db["counters"].find_one_and_update(
            {"_id": "ticket_seq"},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        seq = int(doc.get("seq", 1)) if doc else 1
        return f"TK-{_TICKET_BASE + seq}"
    except Exception:
        _MEMORY_COUNTERS["ticket_seq"] = _MEMORY_COUNTERS.get("ticket_seq", 0) + 1
        return f"TK-{_TICKET_BASE + _MEMORY_COUNTERS['ticket_seq']}"


def clear_all(*, clear_counters: bool = False) -> None:
    """Clear persisted conversations + events. Keeps ticket counter by default
    so ids stay unique across /reset."""
    _MEMORY_CONVOS.clear()
    _MEMORY_EVENTS.clear()
    if clear_counters:
        _MEMORY_COUNTERS["ticket_seq"] = 0
    db = get_db()
    if db is None:
        return
    try:
        db["conversations"].delete_many({})
        db["events"].delete_many({})
        if clear_counters:
            db["counters"].delete_many({"_id": "ticket_seq"})
    except Exception:
        pass


# --- users ------------------------------------------------------------------


def _users_coll() -> Any | None:
    db = get_db()
    if db is None:
        return None
    try:
        return db["users"]
    except Exception:
        return None


def save_user(doc: dict) -> dict:
    """Upsert a user doc keyed by id. Falls back to memory without Mongo.

    Email uniqueness is enforced across both stores: a different id already
    holding the same (case-insensitive) email raises ``ValueError``. Without
    this the in-memory fallback -- which has no unique index -- could hold
    two accounts for one email after a seed race, and a mongo
    ``DuplicateKeyError`` fallback write could do the same.
    """
    record = dict(doc)
    uid = str(record.get("id", ""))
    if not uid:
        return record
    email = str(record.get("email", "")).strip().lower()
    record["email"] = email
    if email:
        try:
            clash = get_user_by_email(email)
        except Exception:
            clash = None
        if clash is not None and str(clash.get("id", "")) != uid:
            raise ValueError("Email already exists")
    coll = _users_coll()
    if coll is None:
        _MEMORY_USERS[uid] = record
        return record
    try:
        coll.update_one({"id": uid}, {"$set": record}, upsert=True)
    except Exception:
        _MEMORY_USERS[uid] = record
    else:
        _MEMORY_USERS[uid] = record
    return record


def get_user_by_email(email: str) -> dict | None:
    """Find a user by email (case-insensitive). Memory + mongo."""
    want = (email or "").strip().lower()
    if not want:
        return None
    for doc in _MEMORY_USERS.values():
        if str(doc.get("email", "")).strip().lower() == want:
            return dict(doc)
    coll = _users_coll()
    if coll is not None:
        try:
            found = coll.find_one({"email": want}, {"_id": 0})
        except Exception:
            found = None
        if found is not None:
            _MEMORY_USERS[str(found.get("id", ""))] = dict(found)
            return dict(found)
    return None


def get_user_by_id(uid: str) -> dict | None:
    """Find a user by id. Memory + mongo."""
    if not uid:
        return None
    mem = _MEMORY_USERS.get(str(uid))
    if mem is not None:
        return dict(mem)
    coll = _users_coll()
    if coll is not None:
        try:
            found = coll.find_one({"id": str(uid)}, {"_id": 0})
        except Exception:
            found = None
        if found is not None:
            _MEMORY_USERS[str(found.get("id", ""))] = dict(found)
            return dict(found)
    return None


def list_users() -> list[dict]:
    """All users, memory + mongo merged by id (never includes password hash)."""
    merged: dict[str, dict] = {k: dict(v) for k, v in _MEMORY_USERS.items()}
    coll = _users_coll()
    if coll is not None:
        try:
            for doc in coll.find({}, {"_id": 0}):
                uid = str(doc.get("id", ""))
                if uid and uid not in merged:
                    merged[uid] = dict(doc)
                elif uid:
                    merged[uid] = dict(doc)
        except Exception:
            pass
    return list(merged.values())


def clear_users() -> None:
    """Clear cached + persisted users. Tests only; never called at runtime."""
    _MEMORY_USERS.clear()
    coll = _users_coll()
    if coll is None:
        return
    try:
        coll.delete_many({})
    except Exception:
        pass
