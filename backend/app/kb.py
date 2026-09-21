"""The knowledge base, and the one thing it refuses to do.

Everything the agent is allowed to say about policy comes through here. It is
the only door to the policy text, which is what makes "show the source used for
its answer" a property of the system rather than a habit of the prompt.

Two decisions worth defending.

Retrieval is keyword scoring over ten short clauses, not embeddings. A vector
store for eleven paragraphs would be ceremony: it adds a model, a build step and
a failure mode to search a page of text, and it would make the citation harder
to trace rather than easier. If the knowledge base grew to a thousand articles
this is the first thing to replace.

And conflicts are declared in the data, not inferred at runtime. KB-03 says a
laptop is replaceable after three years; ASSET-01 says the refresh cycle is four
and early replacement needs Finance. Nothing in the supplied material resolves
that, so a laptop aged between three and four years is genuinely ambiguous. An
agent that picks one and answers confidently is the failure this assignment is
testing for, so the ambiguity is marked in the data and enforced in code.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import db as _db

DATA = Path(__file__).resolve().parent.parent / "data"

_WORDS = re.compile(r"[a-z0-9]+")

# Admin-configured overrides, Mongo-backed with in-memory fallback (same
# pattern as conversations). Base YAML in CLAUSES is never mutated, so the
# data pack stays pristine and tests keep passing against it.
_MEMORY_OVERRIDES: dict[str, dict] = {}


def _overrides_coll() -> Any | None:
    try:
        real = _db.get_db()
    except Exception:
        return None
    if real is None:
        return None
    try:
        return real["kb_overrides"]
    except Exception:
        return None


def list_overrides() -> dict[str, dict]:
    """All admin overrides keyed by clause id (mongo + memory merged)."""
    merged = dict(_MEMORY_OVERRIDES)
    coll = _overrides_coll()
    if coll is not None:
        try:
            for doc in coll.find({}, {"_id": 0}):
                cid = doc.get("id", "")
                if cid:
                    merged[cid] = {k: v for k, v in doc.items() if k != "id"}
        except Exception:
            pass
    return merged


def save_override(clause_id: str, patch: dict) -> dict:
    """Upsert an admin override. Returns the stored patch."""
    global _EFFECTIVE_CACHE
    _MEMORY_OVERRIDES[clause_id] = dict(patch)
    _EFFECTIVE_CACHE = None
    coll = _overrides_coll()
    if coll is not None:
        try:
            coll.update_one({"id": clause_id}, {"$set": dict(patch)}, upsert=True)
        except Exception:
            pass
    return dict(patch)


def clear_overrides() -> None:
    global _EFFECTIVE_CACHE
    _MEMORY_OVERRIDES.clear()
    _EFFECTIVE_CACHE = None
    coll = _overrides_coll()
    if coll is not None:
        try:
            coll.delete_many({})
        except Exception:
            pass


_EFFECTIVE_CACHE: dict[str, Clause] | None = None
_EFFECTIVE_AT: float = 0.0
_EFFECTIVE_TTL = 30.0


def effective_clauses() -> dict[str, Clause]:
    """Base clauses with admin overrides applied (edits merged, new ids added).

    Cached briefly so a single agent turn does not fan out to Mongo once per
    citation; invalidated on every override write.
    """
    global _EFFECTIVE_CACHE, _EFFECTIVE_AT
    now = time.monotonic()
    if _EFFECTIVE_CACHE is not None and now - _EFFECTIVE_AT < _EFFECTIVE_TTL:
        return _EFFECTIVE_CACHE
    out = dict(CLAUSES)
    for cid, patch in list_overrides().items():
        base = out.get(cid)
        if base is None:
            out[cid] = Clause(
                id=cid,
                title=str(patch.get("title", cid)),
                source=str(patch.get("source", "Admin override")),
                authority=str(patch.get("authority", "IT")),
                text=str(patch.get("text", "")),
                keywords=list(patch.get("keywords", [])),
                conflicts_with=list(patch.get("conflicts_with", [])),
            )
        else:
            out[cid] = Clause(
                id=base.id,
                title=str(patch.get("title", base.title)),
                source=base.source,
                authority=str(patch.get("authority", base.authority)),
                text=str(patch.get("text", base.text)),
                keywords=list(patch.get("keywords", base.keywords)),
                conflicts_with=list(patch.get("conflicts_with", base.conflicts_with)),
            )
    _EFFECTIVE_CACHE = out
    _EFFECTIVE_AT = time.monotonic()
    return out


@dataclass(frozen=True)
class Clause:
    """One citable statement of policy."""

    id: str
    title: str
    source: str
    authority: str
    text: str
    keywords: list[str] = field(default_factory=list)
    conflicts_with: list[str] = field(default_factory=list)

    def cite(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "source": self.source,
            "authority": self.authority,
            "text": " ".join(self.text.split()),
        }


def _load(name: str) -> list[dict]:
    return yaml.safe_load((DATA / name).read_text(encoding="utf-8")) or []


CLAUSES: dict[str, Clause] = {
    row["id"]: Clause(**row) for row in _load("knowledge_base.yaml")
}
REQUESTS: list[dict] = _load("requests.yaml")
TICKETS: list[dict] = _load("tickets.yaml")


def search(query: str, limit: int = 3) -> list[Clause]:
    """The clauses most likely to govern this request.

    Scored on declared keywords first and raw word overlap second. Keywords win
    because "my laptop is dead" shares no vocabulary with "eligible for
    replacement after 3 years" -- the words a person uses for their problem are
    never the words a policy uses for the rule.
    """
    words = set(_WORDS.findall(query.lower()))
    if not words:
        return []

    scored: list[tuple[float, Clause]] = []
    for clause in effective_clauses().values():
        hits = sum(3 for k in clause.keywords if k in query.lower())
        hits += len(words & set(_WORDS.findall(clause.text.lower()))) * 0.25
        hits += len(words & set(_WORDS.findall(clause.title.lower()))) * 2
        if hits:
            scored.append((hits, clause))

    scored.sort(key=lambda pair: (-pair[0], pair[1].id))
    return [clause for _, clause in scored[:limit]]


def conflicts_among(ids: list[str]) -> list[tuple[Clause, Clause]]:
    """Pairs of cited clauses that contradict each other.

    Returned rather than raised: the caller decides what to do about it, and the
    answer is always to show both and hand the decision to a person.
    """
    found: list[tuple[Clause, Clause]] = []
    seen: set[frozenset[str]] = set()
    clauses = effective_clauses()
    for one in ids:
        first = clauses.get(one)
        if first is None:
            continue
        for other in first.conflicts_with:
            pair = frozenset({one, other})
            if other in ids and pair not in seen:
                seen.add(pair)
                other_clause = clauses.get(other)
                if other_clause is not None:
                    found.append((first, other_clause))
    return found


def precedents(query: str, limit: int = 2) -> list[dict]:
    """Closed tickets worth knowing about before deciding this one.

    A service desk that contradicts its own last decision is worse than one that
    is slow, so prior rulings are context the agent gets for free.
    """
    words = set(_WORDS.findall(query.lower()))
    scored = []
    for ticket in TICKETS:
        if not ticket.get("precedent"):
            continue
        overlap = len(words & set(_WORDS.findall(ticket["summary"].lower())))
        if overlap:
            scored.append((overlap, ticket))
    scored.sort(key=lambda pair: -pair[0])
    return [ticket for _, ticket in scored[:limit]]


def clause(clause_id: str) -> Clause | None:
    return effective_clauses().get(clause_id)
