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

from . import kb

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
    # The date the employee raised it, from the data pack -- not when this
    # conversation object was made. The gap between the two is the point: a
    # request opened on Monday and looked at on Friday is four days old.
    opened: str = ""
    turns: list[dict] = field(default_factory=list)
    actions: list[dict] = field(default_factory=list)
    cited: list[str] = field(default_factory=list)
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
    opened_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not self.best_match:
            owner = kb.search(self.text, limit=1)
            self.best_match = owner[0].id if owner else ""

    def log(self, tool: str, detail: dict) -> None:
        self.actions.append(
            {"at": datetime.now(UTC).isoformat(), "tool": tool, **detail}
        )

    def is_closed(self) -> bool:
        return self.outcome is not None


def _numbers_in(text: str) -> set[tuple[str, str]]:
    return {(m.group(1), m.group(2).lower().rstrip("s")) for m in _NUMBERS.finditer(text)}


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
        return {
            "refused": "followup_budget_spent",
            "detail": f"You have already asked {MAX_FOLLOWUPS} questions. Resolve with "
            "what you have, or escalate saying what is still unknown.",
        }
    convo.followups_asked += 1
    convo.log("ask_followup", {"question": question, "why": why})
    return {"asked": question, "remaining": MAX_FOLLOWUPS - convo.followups_asked}


def resolve(convo: Conversation, answer: str, cites: list[str]) -> dict:
    """Close the request with an answer.

    Refuses more often than it succeeds, and every refusal names its reason.
    """
    if not cites:
        return {
            "refused": "no_citation",
            "detail": "An answer has to rest on a clause. Call find_policy first and "
            "cite what you used.",
        }

    unknown = [c for c in cites if kb.clause(c) is None]
    if unknown:
        return {
            "refused": "unknown_clause",
            "detail": f"No such clause: {', '.join(unknown)}. Cite only ids that find_policy returned.",
        }

    # Against everything retrieved, not just what this answer chose to cite.
    # Otherwise the conflict is dodged by citing one side of it, which is the
    # easiest mistake to make and the hardest to spot in a transcript: the
    # answer looks properly sourced and is still wrong.
    conflicts = kb.conflicts_among(sorted({*cites, *convo.cited}))
    if conflicts:
        a, b = conflicts[0]
        return {
            "refused": "conflicting_sources",
            "detail": f"{a.id} and {b.id} contradict each other on this, so there is no "
            "answer to give. Escalate with both sources and let a person decide.",
            "conflict": [a.cite(), b.cite()],
        }

    clauses = [kb.clause(c) for c in cites]
    outside = [c for c in clauses if c and c.authority not in ("IT", "Employee self-service")]
    if outside:
        owner = outside[0]
        return {
            "refused": "not_our_authority",
            "detail": f"{owner.id} says this is {owner.authority}'s to grant, not IT's. "
            "Tell the employee who owns it and what they need to do, then route it.",
        }

    # The clause that matched best is not cited, and it belongs to someone else.
    # Answering from the second-best clause is how a request that Finance owns
    # gets closed by IT with a plausible-sounding answer about passwords.
    best = kb.clause(convo.best_match)
    if best and best.id not in cites and best.authority not in ("IT", "Employee self-service"):
        return {
            "refused": "ignored_the_owner",
            "detail": f"What the employee actually asked about is covered by {best.id}, "
            f"which says this is {best.authority}'s, not IT's. Answer that before "
            f"falling back on {', '.join(cites)}, or route it to {best.authority}.",
            "clause": best.cite(),
        }

    stated = _numbers_in(answer)
    supported = set().union(*(_numbers_in(c.text) for c in clauses if c)) if clauses else set()
    invented = stated - supported
    if invented:
        pretty = ", ".join(f"{n} {unit}" for n, unit in sorted(invented))
        return {
            "refused": "unsupported_figure",
            "detail": f"The answer states {pretty}, which appears in none of the clauses "
            "you cited. Quote the figure the policy actually gives, or drop it.",
        }

    convo.outcome = "resolved"
    convo.log("resolve", {"answer": answer, "cites": cites})
    return {"resolved": True, "cites": cites}


def raise_ticket(convo: Conversation, category: str, summary: str, priority: str) -> dict:
    """Create a structured ticket.

    Refused when the cited policy says no ticket is needed: the fastest service
    desk is the one that does not open work it does not have to.
    """
    if priority not in ("low", "normal", "high", "urgent"):
        return {"refused": "bad_priority", "detail": "Priority is low, normal, high or urgent."}

    for cited in convo.cited:
        clause = kb.clause(cited)
        if clause and "no it ticket required" in clause.text.lower():
            return {
                "refused": "no_ticket_needed",
                "detail": f"{clause.id} says this needs no ticket. Tell them how to do it "
                "themselves instead of opening one.",
            }

    ticket_id = f"TK-{1052 + len([a for a in convo.actions if a['tool'] == 'raise_ticket'])}"
    convo.outcome = "ticket_raised"
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
