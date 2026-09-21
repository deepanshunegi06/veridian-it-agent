"""What the agent is allowed to do, and what it is not allowed to get away with.

The product's promises live in this file rather than in the prompt, because a
prompt is a request and a function signature is a rule. A model having a bad day
can ignore "always cite your source"; it cannot ignore a `resolve` that returns
an error when the citation is missing.

Three rules are enforced here:

  An answer must rest on a clause that was actually retrieved. `resolve` checks
  the cited ids exist and that the answer does not assert a number the clauses
  do not contain -- the specific way a model invents policy is to keep the shape
  of the rule and change the threshold.

  Contradicting sources cannot be resolved, only escalated. If the cited clauses
  are marked as conflicting, `resolve` refuses and says which pair. This is the
  laptop case, and it is decided by data rather than by whether the model
  happened to notice.

  Work that belongs to another function is routed, not answered. Every clause
  carries an authority; when it is not IT, `resolve` says so.

Each refusal hands back the reason and the material, so the model can correct
itself on the next turn rather than failing silently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from . import db, kb

# Thresholds a model is likely to restate from memory rather than from the text.
_NUMBERS = re.compile(r"\b(\d+(?:\.\d+)?)\s*(year|yr|day|gb|attempt|week|month|hour)s?\b", re.I)

MAX_FOLLOWUPS = 2


@dataclass
class Conversation:
    """One employee request being worked. The audit trail is the object."""

    request_id: str
    employee: str
    text: str
    initial_action: str = ""
    # Who owns this thread. Derived from auth at creation time, never from a
    # request body, so one employee cannot spoof another's name into ownership.
    owner_user_id: str | None = None
    # The date the employee raised it, from the data pack -- not when this
    # conversation object was made. The gap between the two is the point: a
    # request opened on Monday and looked at on Friday is four days old.
    opened: str = ""
    turns: list[dict] = field(default_factory=list)
    actions: list[dict] = field(default_factory=list)
    cited: list[str] = field(default_factory=list)
    # Full thread visible to employee + IT admin: every user, agent, human and
    # system message in order. `turns` stays as the agent-reply audit subset.
    # Each entry: {seq, speaker, name, text, at}. Speaker is one of
    # user | agent | human | system.
    messages: list[dict] = field(default_factory=list)
    # IT handoff state. None until an admin joins; then the admin's name.
    assigned_to: str | None = None
    # Who joined. Names are display only; enforcement uses this id.
    assigned_to_user_id: str | None = None
    # Ticket lifecycle for this thread: None | open | assigned | resolved.
    ticket_status: str | None = None
    # Automatic ticket for every employee thread (POST /threads). Allocated
    # from the global counter at creation time, independent of the AI run, so
    # the employee always has a ticket even while the agent asks follow-ups.
    # Migration-safe: old docs simply lack these keys and load as None.
    ticket_id: str | None = None
    ticket_category: str | None = None
    ticket_summary: str | None = None
    ticket_priority: str | None = None
    # Customer-safe concise ticket name generated once from the first employee
    # message (e.g. "Laptop won't turn on"). Set by agent.ensure_ticket_title
    # before the first user-facing response; follow-ups never overwrite it.
    # Migration-safe: old docs simply lack the key and load as None.
    ticket_title: str | None = None
    # The clause that best matches what the EMPLOYEE wrote, computed once when
    # the request arrives. Deliberately not taken from the agent's own search:
    # a model that wants a particular answer writes a query that finds it. Asked
    # about the expense tool, it searched "expense tool login invalid credentials
    # password reset policy", floated the password clause to the top, and
    # answered from that -- while KB-08 sat there saying the whole thing is
    # Finance's. Grounding this in the employee's words closes that door.
    best_match: str = ""
    followups_asked: int = 0
    outcome: str | None = None
    # Who closed the thread: None | "ai" | "human". Migration-safe: old docs
    # lack the key and load as None, which reopens as AI-resolved behaviour.
    resolution_source: str | None = None
    opened_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not self.best_match:
            owner = kb.search(self.text, limit=1)
            self.best_match = owner[0].id if owner else ""
        if not self.messages and self.text:
            self.say("user", self.text, name=self.employee)

    def say(self, speaker: str, text: str, name: str = "") -> dict:
        """Append one visible thread message. Never refuses, never closes."""
        msg = {
            "seq": len(self.messages),
            "speaker": speaker,
            "name": name,
            "text": text,
            "at": datetime.now(UTC).isoformat(),
        }
        self.messages.append(msg)
        return msg

    def log(self, tool: str, detail: dict) -> None:
        self.actions.append(
            {"at": datetime.now(UTC).isoformat(), "tool": tool, **detail}
        )

    def is_closed(self) -> bool:
        return self.outcome is not None


def _refuse(convo: Conversation, tool: str, refused: str, detail: str, **extra: Any) -> dict:
    """Log a refusal into the audit trail without closing the request.

    Refusals are corrections, not outcomes: the request stays open and
    ``convo.outcome`` is untouched. Persisting them here is what makes
    ``/stats`` and ``GET /requests/{id}`` honest without watching the stream.
    """
    convo.log(tool, {"refused": refused, "detail": detail, **extra})
    return {"refused": refused, "detail": detail, **extra}


def _numbers_in(text: str) -> set[tuple[str, str]]:
    return {(m.group(1), m.group(2).lower().rstrip("s")) for m in _NUMBERS.finditer(text)}


# --- grounded material-conflict check for KB-03 / ASSET-01 ----------------------
#
# KB-03 permits laptop replacement after 3 years; ASSET-01 enforces a 4-year
# refresh cycle with Finance sign-off for early replacement. The ambiguity is
# material only when the laptop is between 3 and 4 years old (or when the age
# is unknown): there KB-03 says eligible while ASSET-01 still demands Finance.
# At >= 4 years both policies support replacement, so the pair is immaterial
# and a properly cited resolve may proceed (still subject to the number /
# citation / authority guardrails below).
#
# A verified hardware failure does NOT clear the conflict in the 3-4 window:
# KB-03's failure exception does not override ASSET-01's Finance sign-off for
# early replacement outside the 4-year cycle, so the Finance/IT approval
# ambiguity remains and must still escalate.
#
# Migration-safe: only employee-provided texts (initial text + user messages)
# are inspected via getattr, so old Conversation docs lacking messages/text
# simply yield no age and stay conservative (refuse). Unknown/unrelated
# clause pairs are always treated as material.

_AGE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[-–]?\s*(?:years?|yrs?)\b", re.I)

# Words that, near a year figure, suggest the figure is the device's age
# rather than e.g. tenure ("worked here 5 years").
_AGE_CUE_RE = re.compile(
    r"laptop|device|hardware|machine|had\b|have\b|has\b|old\b|age\b|aged\b|"
    r"issued|bought|got\b|owned|own\b|using|use\b|service|replacement|refresh|"
    r"since\b|for\b",
    re.I,
)


def _employee_texts(convo: Conversation) -> list[str]:
    """Employee-provided texts only (never the agent's queries)."""
    out: list[str] = []
    try:
        initial = getattr(convo, "text", "") or ""
    except Exception:
        initial = ""
    if isinstance(initial, str) and initial.strip():
        out.append(initial)
    try:
        messages = getattr(convo, "messages", None) or []
    except Exception:
        messages = []
    try:
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            if msg.get("speaker") != "user":
                continue
            text = msg.get("text", "")
            if isinstance(text, str) and text.strip():
                out.append(text)
    except Exception:
        pass
    return out


def _laptop_ages_in_text(text: str) -> list[float]:
    """Year figures in one employee text that plausibly state laptop age."""
    ages: list[float] = []
    if not isinstance(text, str) or not text:
        return ages
    for match in _AGE_RE.finditer(text):
        try:
            value = float(match.group(1))
        except ValueError:
            continue
        start, end = match.span()
        window = text[max(0, start - 50) : end + 50]
        if _AGE_CUE_RE.search(window):
            ages.append(value)
    return ages


def _laptop_ages(convo: Conversation) -> list[float]:
    """All plausible laptop-age mentions across employee texts."""
    ages: list[float] = []
    for text in _employee_texts(convo):
        ages.extend(_laptop_ages_in_text(text))
    # Follow-up answers are often bare ("4.5 years old", "4.5 years"): if the
    # overall employee context mentions a laptop but the bare answer's window
    # lacked a cue, re-scan leniently for short age-only replies.
    if not ages:
        try:
            combined = " ".join(_employee_texts(convo)).lower()
        except Exception:
            combined = ""
        if "laptop" in combined or "hardware" in combined:
            for text in _employee_texts(convo):
                if not isinstance(text, str):
                    continue
                stripped = text.strip()
                if len(stripped) > 60:
                    continue
                for match in _AGE_RE.finditer(stripped):
                    try:
                        ages.append(float(match.group(1)))
                    except ValueError:
                        continue
    return ages


def _kb03_asset01_resolvable(convo: Conversation) -> bool:
    """True only when employee context clearly establishes age >= 4 years.

    Conservative by design: missing/unclear ages, any age below 4, or
    admin-edited clause texts that no longer state the 3-year / 4-year
    thresholds all return False (material conflict, must escalate).
    """
    try:
        kb03 = kb.clause("KB-03")
        asset01 = kb.clause("ASSET-01")
    except Exception:
        return False
    if kb03 is None or asset01 is None:
        return False
    # Guard against admin overrides changing the thresholds this reasoning
    # rests on: only apply when the clauses still state 3 and 4 years.
    # NOTE: the shared _NUMBERS guardrail regex does not match hyphenated
    # "4-year", which is how ASSET-01 phrases its cycle, so test the raw
    # clause text with a hyphen-tolerant pattern here (guardrail itself kept).
    try:
        kb03_text = kb03.text or ""
        asset_text = asset01.text or ""
    except Exception:
        return False
    try:
        if not re.search(r"3\s*[-–]?\s*years?\b", kb03_text, re.I):
            return False
        if not re.search(r"4\s*[-–]?\s*years?\b", asset_text, re.I):
            return False
    except Exception:
        return False
    try:
        ages = _laptop_ages(convo)
    except Exception:
        return False
    if not ages:
        return False
    # Every observed age must be >= 4: a single 3.x mention keeps it ambiguous
    # even if another message claims >= 4 (contradictory employee facts).
    return bool(ages) and min(ages) >= 4.0


def _is_material_conflict(pair: tuple[Any, Any], convo: Conversation) -> bool:
    """Whether a conflicting clause pair actually blocks this resolve."""
    try:
        ids = {pair[0].id, pair[1].id}
    except Exception:
        return True
    if ids == {"KB-03", "ASSET-01"}:
        return not _kb03_asset01_resolvable(convo)
    return True


# --- the tools ------------------------------------------------------------------


def find_policy(convo: Conversation, query: str) -> dict:
    """Search the knowledge base. The only way to obtain policy text.

    Returns the clauses, any conflict between them, and any prior ticket that
    already decided something similar.
    """
    clauses = kb.search(query)
    convo.cited = sorted({*convo.cited, *(c.id for c in clauses)})
    conflicts = kb.conflicts_among([c.id for c in clauses])
    prior = kb.precedents(query)

    result = {
        "clauses": [c.cite() for c in clauses],
        "precedents": [
            {"id": t["id"], "summary": t["summary"], "status": t["status"], "note": t["precedent"]}
            for t in prior
        ],
    }
    if conflicts:
        result["conflict"] = [
            {
                "between": [a.id, b.id],
                "why": f"{a.id} ({a.source}) and {b.id} ({b.source}) give different rules "
                f"for the same situation, and nothing here says which wins.",
            }
            for a, b in conflicts
        ]
    if not clauses:
        result["note"] = (
            "Nothing in the knowledge base matches that. Ask the employee what "
            "they were doing when it failed rather than guessing at a policy."
        )
    convo.log("find_policy", {"query": query, "found": [c.id for c in clauses]})
    return result


def ask_followup(convo: Conversation, question: str, why: str) -> dict:
    """Ask the employee one thing.

    Budgeted, because an employee who wanted a form would have filled one in.
    Past the budget the agent has to act on what it has or hand over.
    """
    if convo.followups_asked >= MAX_FOLLOWUPS:
        return _refuse(
            convo,
            "ask_followup",
            "followup_budget_spent",
            f"You have already asked {MAX_FOLLOWUPS} questions. Resolve with "
            "what you have, or escalate saying what is still unknown.",
        )
    convo.followups_asked += 1
    convo.log("ask_followup", {"question": question, "why": why})
    return {"asked": question, "remaining": MAX_FOLLOWUPS - convo.followups_asked}


def resolve(convo: Conversation, answer: str, cites: list[str]) -> dict:
    """Close the request with an answer.

    Refuses more often than it succeeds, and every refusal names its reason.
    """
    if not cites:
        return _refuse(
            convo,
            "resolve",
            "no_citation",
            "An answer has to rest on a clause. Call find_policy first and "
            "cite what you used.",
        )

    unknown = [c for c in cites if kb.clause(c) is None]
    if unknown:
        return _refuse(
            convo,
            "resolve",
            "unknown_clause",
            f"No such clause: {', '.join(unknown)}. Cite only ids that find_policy returned.",
        )

    # Against everything retrieved, not just what this answer chose to cite.
    # Otherwise the conflict is dodged by citing one side of it, which is the
    # easiest mistake to make and the hardest to spot in a transcript: the
    # answer looks properly sourced and is still wrong.
    # Grounded exception: the KB-03/ASSET-01 pair is immaterial when the
    # employee context clearly establishes laptop age >= 4 years (both
    # policies then support replacement). All other pairs stay conservative.
    conflicts = kb.conflicts_among(sorted({*cites, *convo.cited}))
    material = [pair for pair in conflicts if _is_material_conflict(pair, convo)]
    if material:
        a, b = material[0]
        return _refuse(
            convo,
            "resolve",
            "conflicting_sources",
            f"{a.id} and {b.id} contradict each other on this, so there is no "
            "answer to give. Escalate with both sources and let a person decide.",
            conflict=[a.cite(), b.cite()],
        )

    clauses = [kb.clause(c) for c in cites]
    # ASSET-01 carries joint "Finance and IT" authority, but its own text
    # conditions Finance sign-off on early replacement outside the 4-year
    # cycle. When age >= 4 is clearly established there is no early
    # replacement, so IT may resolve citing it alongside KB-03. Otherwise the
    # existing guardrail stands.
    asset_excused = _kb03_asset01_resolvable(convo)
    outside = [
        c
        for c in clauses
        if c
        and c.authority not in ("IT", "Employee self-service")
        and not (asset_excused and c.id == "ASSET-01")
    ]
    if outside:
        owner = outside[0]
        return _refuse(
            convo,
            "resolve",
            "not_our_authority",
            f"{owner.id} says this is {owner.authority}'s to grant, not IT's. "
            "Tell the employee who owns it and what they need to do, then route it.",
        )

    # The clause that matched best is not cited, and it belongs to someone else.
    # Answering from the second-best clause is how a request that Finance owns
    # gets closed by IT with a plausible-sounding answer about passwords.
    # Narrow exception: ASSET-01's joint "Finance and IT" authority is
    # satisfied alongside KB-03 when age >= 4 is clearly established (no early
    # replacement needing Finance), so it does not trigger ignored_the_owner.
    best = kb.clause(convo.best_match)
    if best and best.id not in cites and best.authority not in ("IT", "Employee self-service"):
        if not (asset_excused and best.id == "ASSET-01"):
            return _refuse(
                convo,
                "resolve",
                "ignored_the_owner",
                f"What the employee actually asked about is covered by {best.id}, "
                f"which says this is {best.authority}'s, not IT's. Answer that before "
                f"falling back on {', '.join(cites)}, or route it to {best.authority}.",
                clause=best.cite(),
            )

    stated = _numbers_in(answer)
    supported = set().union(*(_numbers_in(c.text) for c in clauses if c)) if clauses else set()
    invented = stated - supported
    if invented:
        pretty = ", ".join(f"{n} {unit}" for n, unit in sorted(invented))
        return _refuse(
            convo,
            "resolve",
            "unsupported_figure",
            f"The answer states {pretty}, which appears in none of the clauses "
            "you cited. Quote the figure the policy actually gives, or drop it.",
        )

    convo.outcome = "resolved"
    convo.resolution_source = "ai"
    if convo.ticket_status != "resolved":
        convo.ticket_status = "resolved"
    convo.log("resolve", {"answer": answer, "cites": cites})
    return {"resolved": True, "cites": cites}


def ensure_auto_ticket(
    convo: Conversation,
    summary: str | None = None,
    category: str | None = None,
    priority: str | None = None,
) -> str:
    """Attach the automatic employee ticket, allocating exactly once.

    Called at thread creation (and lazily on the next message for threads
    that predate the field). Uses the global counter, sets status to open,
    and never touches ``outcome`` -- the AI may still ask follow-ups, resolve
    or escalate according to policy, but creation itself is never an
    escalation. No audit ``actions`` entry is logged so first-message
    deduplication (which requires empty turns/actions) stays intact.
    """

    existing = getattr(convo, "ticket_id", None)
    if isinstance(existing, str) and existing:
        return existing
    ticket_id = db.next_ticket_id()
    convo.ticket_id = ticket_id
    base = (summary if summary is not None else (convo.text or "")).strip()
    convo.ticket_summary = base[:200] if base else ""
    convo.ticket_category = category or getattr(convo, "ticket_category", None) or "general"
    convo.ticket_priority = priority or getattr(convo, "ticket_priority", None) or "normal"
    if convo.ticket_status is None:
        convo.ticket_status = "open"
    # outcome deliberately untouched: a fresh ticket is open, not escalated.
    return ticket_id


def raise_ticket(convo: Conversation, category: str, summary: str, priority: str) -> dict:
    """Create a structured ticket.

    Refused when the cited policy says no ticket is needed: the fastest service
    desk is the one that does not open work it does not have to.

    When the thread already carries an automatic ticket, the existing id is
    reused (no second counter increment) and the action is associated with it;
    refusal checks run first so ``bad_priority``/``no_ticket_needed`` keep
    their semantics. Threads without an auto ticket (direct tool tests, legacy
    docs) allocate exactly as before.
    """
    if priority not in ("low", "normal", "high", "urgent"):
        return _refuse(
            convo, "raise_ticket", "bad_priority", "Priority is low, normal, high or urgent."
        )

    for cited in convo.cited:
        clause = kb.clause(cited)
        if clause and "no it ticket required" in clause.text.lower():
            return _refuse(
                convo,
                "raise_ticket",
                "no_ticket_needed",
                f"{clause.id} says this needs no ticket. Tell them how to do it "
                "themselves instead of opening one.",
            )

    # Global counter (Mongo-backed with in-memory fallback). The old
    # per-conversation count restarted at TK-1052 for every request.
    existing = getattr(convo, "ticket_id", None)
    if isinstance(existing, str) and existing:
        ticket_id = existing
    else:
        ticket_id = db.next_ticket_id()
        convo.ticket_id = ticket_id
    convo.ticket_category = category
    convo.ticket_summary = summary
    convo.ticket_priority = priority
    convo.outcome = "ticket_raised"
    convo.ticket_status = "open"
    convo.log(
        "raise_ticket",
        {"ticket": ticket_id, "category": category, "summary": summary, "priority": priority},
    )
    return {"ticket": ticket_id, "category": category, "priority": priority}


def escalate(convo: Conversation, reason: str, to: str, cites: list[str]) -> dict:
    """Hand the decision to a person, with the material they need to make it.

    Never refused. Escalating when it was not necessary costs somebody a minute;
    not escalating when it was costs the company an approval nobody authorised.
    """
    convo.outcome = "escalated"
    if convo.ticket_status is None:
        convo.ticket_status = "open"
    convo.log("escalate", {"reason": reason, "to": to, "cites": cites})
    return {
        "escalated_to": to,
        "reason": reason,
        "sources": [kb.clause(c).cite() for c in cites if kb.clause(c)],
    }


TOOLS: dict[str, Any] = {
    "find_policy": find_policy,
    "ask_followup": ask_followup,
    "resolve": resolve,
    "raise_ticket": raise_ticket,
    "escalate": escalate,
}
