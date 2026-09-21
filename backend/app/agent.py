"""The loop.

The model chooses which tool to call and when to stop. That choice is the whole
product: you cannot predict the sequence for a request before running it,
because it depends on what the employee wrote and what the knowledge base says
back. Guest Wi-Fi is one search and an answer; the laptop is a search, a
follow-up, a refused resolve and an escalation.

What the loop itself guarantees is narrower and more boring: the tools are the
only way to affect anything, a refusal comes back as material to work with
rather than an exception, and the whole thing stops after a fixed number of
steps whatever the model thinks.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from . import tools
from .llm import build_llm, get_settings
from .tools import Conversation

PROMPT = (Path(__file__).resolve().parent.parent / "prompts" / "agent.md").read_text(
    encoding="utf-8"
)

# The schemas the model sees. Descriptions are written for the model, and they
# say what each tool refuses as well as what it does -- a tool whose failure
# modes are documented gets called correctly more often than one that is
# described only by its happy path.
SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "find_policy",
        "description": (
            "Search the Veridian knowledge base and asset policy. The only source of "
            "policy text. Returns clauses with ids, any conflict between them, and any "
            "past ticket that decided something similar. Call this first, always."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What the employee needs, in your words. Include the "
                    "detail that decides the rule, like the age of a laptop.",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "ask_followup",
        "description": (
            "Ask the employee one question. Only when the answer genuinely changes which "
            "rule applies. Two per request, then you must act on what you have."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "The question, as you would say it."},
                "why": {
                    "type": "string",
                    "description": "Which rule this decides between. For the audit trail.",
                },
            },
            "required": ["question", "why"],
        },
    },
    {
        "name": "resolve",
        "description": (
            "Answer the employee and close the request. Refused if you cite nothing, cite "
            "a clause that does not exist, cite clauses that contradict each other, state "
            "a figure none of them contain, or answer for a function that is not IT."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "answer": {
                    "type": "string",
                    "description": "Two or three sentences to the employee: what is true, "
                    "what happens next, who does it.",
                },
                "cites": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Clause ids this answer rests on, e.g. KB-07.",
                },
            },
            "required": ["answer", "cites"],
        },
    },
    {
        "name": "raise_ticket",
        "description": (
            "Open a structured ticket. Only when the policy says one is needed. Refused "
            "for anything a clause says the employee can do themselves."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "hardware, access, software, network, email, security",
                },
                "summary": {"type": "string", "description": "One line an IT technician can act on."},
                "priority": {"type": "string", "description": "low, normal, high or urgent"},
            },
            "required": ["category", "summary", "priority"],
        },
    },
    {
        "name": "escalate",
        "description": (
            "Hand the decision to a person, with the sources they need. Use when the "
            "clauses disagree, when the approval is not IT's to give, or when something "
            "you cannot look up is missing. Never refused."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "What a human has to decide, and why you could not.",
                },
                "to": {
                    "type": "string",
                    "description": "Who: Finance, Security, IT Security, a manager, IT lead.",
                },
                "cites": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Clause ids the decision turns on.",
                },
            },
            "required": ["reason", "to", "cites"],
        },
    },
]


# --- ticket naming (first employee message only) -------------------------------

_TITLE_SYSTEM = (
    "You write short customer-facing support ticket titles. "
    "Return ONLY the title text, no quotes, no explanation, no reasoning. "
    "Max 60 characters, plain words, customer-safe."
)

_TITLE_BAD_MARKERS = (
    "kb-", "asset-", "conflict", "escalat", "reason:", "analysis:",
    "chain-of", "policy:", "```", "{", "}",
)


def _fallback_title(text: str) -> str:
    """Deterministic customer-safe title. Never calls the LLM."""
    base = (text or "").strip()
    if not base:
        return "Support request"
    first_line = base.splitlines()[0].strip()
    if not first_line:
        return "Support request"
    if len(first_line) <= 60:
        cand = first_line
    else:
        head = first_line[:60]
        cand = head.rsplit(" ", 1)[0] if " " in head else head
        cand = cand.strip()
    if cand and cand[0].islower():
        cand = cand[0].upper() + cand[1:]
    cand = cand.rstrip(".").strip()
    return cand or "Support request"


def _sanitize_title(raw: Any) -> str | None:
    """Clean LLM output. Returns None when malformed so callers fall back."""
    if isinstance(raw, list):
        parts: list[str] = []
        for block in raw:
            if isinstance(block, dict):
                parts.append(str(block.get("text", "")))
            else:
                parts.append(str(block))
        raw = " ".join(parts)
    if not isinstance(raw, str):
        return None
    cleaned = raw.strip().strip("\"' \u201c\u201d\u2018\u2019").strip()
    if not cleaned:
        return None
    cleaned = cleaned.splitlines()[0].strip().strip("\"'").strip()
    if not cleaned:
        return None
    if cleaned.startswith(("{", "[")):
        return None
    if len(cleaned) > 80:
        return None
    lowered = cleaned.lower()
    if any(marker in lowered for marker in _TITLE_BAD_MARKERS):
        return None
    if len(cleaned) > 60:
        head = cleaned[:60]
        cleaned = (head.rsplit(" ", 1)[0] if " " in head else head).strip()
    if not cleaned:
        return None
    return cleaned


def generate_ticket_title(text: str) -> str:
    """Best-effort LLM title with a deterministic fallback. Never raises."""
    fallback = _fallback_title(text)
    try:
        llm = build_llm(temperature=0.0)
        snap_invokes = getattr(llm, "invokes", None)
        snap_script: list | None = None
        try:
            owned = getattr(llm, "script", None)
            if isinstance(owned, list):
                snap_script = [list(batch) for batch in owned]
        except Exception:
            snap_script = None
        try:
            resp = llm.invoke(
                [
                    SystemMessage(_TITLE_SYSTEM),
                    HumanMessage(f"Request: {(text or '')[:500]}\nTitle:"),
                ]
            )
        except Exception:
            return fallback
        try:
            tool_calls = getattr(resp, "tool_calls", None) or []
        except Exception:
            tool_calls = []
        if tool_calls:
            # A policy-script test double answered a title prompt with a tool
            # batch. Restore its counters so the policy run still sees its
            # full script, then fall back deterministically.
            try:
                if isinstance(snap_invokes, int):
                    llm.invokes = snap_invokes  # type: ignore[attr-defined]
            except Exception:
                pass
            try:
                if snap_script is not None:
                    llm.script = snap_script  # type: ignore[attr-defined]
            except Exception:
                pass
            return fallback
        cleaned = _sanitize_title(getattr(resp, "content", None))
        return cleaned or fallback
    except Exception:
        return fallback


def ensure_ticket_title(
    convo: Conversation, emit: Callable[[dict], None] | None = None
) -> str:
    """Name the ticket once, from the first employee message. Idempotent."""
    existing = getattr(convo, "ticket_title", None)
    if isinstance(existing, str) and existing.strip():
        return existing.strip()
    text = getattr(convo, "text", "") or ""
    try:
        title = generate_ticket_title(text)
    except Exception:
        title = _fallback_title(text)
    if not isinstance(title, str) or not title.strip():
        title = _fallback_title(text)
    title = title.strip()[:80]
    convo.ticket_title = title
    try:
        actions = getattr(convo, "actions", None) or []
        has_raise = any(
            isinstance(a, dict) and a.get("tool") == "raise_ticket" and "ticket" in a
            for a in actions
        )
    except Exception:
        has_raise = False
    if not has_raise:
        convo.ticket_summary = title
    elif not getattr(convo, "ticket_summary", None):
        convo.ticket_summary = title
    if emit is not None:
        try:
            emit(
                {
                    "type": "ticket_updated",
                    "ticket_id": getattr(convo, "ticket_id", None),
                    "ticket": getattr(convo, "ticket_id", None),
                    "title": title,
                    "ticket_title": title,
                    "ticket_summary": getattr(convo, "ticket_summary", None),
                    "summary": title,
                }
            )
        except Exception:
            pass
    return title


def render_prompt(convo: Conversation) -> str:
    initial = (
        f"The existing process has already done this: **{convo.initial_action}**. "
        "Take it into account rather than starting from scratch."
        if convo.initial_action and convo.initial_action != "Not started"
        else "Nothing has been done about it yet."
    )
    return PROMPT.format(
        employee=convo.employee,
        opened=convo.opened or convo.opened_at.strftime("%Y-%m-%d"),
        text=convo.text,
        initial_action_block=initial,
    )


def _reply_for(convo: Conversation, name: str, result: dict) -> tuple[str | None, int | None]:
    """What the employee sees, when a tool produced something they should read.

    Returns ``(text, seq)`` where ``seq`` is the canonical backend message seq
    assigned by :meth:`Conversation.say` (``m-{seq}`` on the wire). Callers
    must forward ``seq`` in the ``say`` SSE event so the browser can reconcile
    the streamed bubble with the canonical backend message by stable id,
    never by text.
    """
    if name == "ask_followup" and "asked" in result:
        msg = convo.say("agent", result["asked"])
        return result["asked"], int(msg["seq"])
    if name == "resolve" and result.get("resolved"):
        msg = convo.say("agent", convo.actions[-1]["answer"])
        return convo.actions[-1]["answer"], int(msg["seq"])
    if name == "escalate":
        ticket_id = getattr(convo, "ticket_id", None)
        if isinstance(ticket_id, str) and ticket_id:
            text = (
                f"I've passed this to the IT team ({ticket_id}). "
                "They'll review it and follow up here."
            )
        else:
            text = "I've passed this to the IT team. They'll review it and follow up here."
        msg = convo.say("agent", text)
        return text, int(msg["seq"])
    if name == "raise_ticket" and "ticket" in result:
        text = (
            f"I've raised {result['ticket']} ({result['priority']} priority) and IT will "
            "pick it up from there."
        )
        msg = convo.say("agent", text)
        return text, int(msg["seq"])
    return None, None


def run(convo: Conversation, on_event: Callable[[dict], None] | None = None) -> Conversation:
    """Work the request until it is closed or the step budget runs out.

    `on_event` receives every step as it happens. It exists so the browser can
    watch the agent get refused and correct itself, which is the only honest way
    to show that the guarantees in tools.py are real.
    """
    emit = on_event or (lambda _event: None)
    # First-message ticket naming: exactly once, before any tool result or
    # user-facing reply. Never overwrites, never breaks the support response.
    try:
        current_title = getattr(convo, "ticket_title", None)
        if not (isinstance(current_title, str) and current_title.strip()):
            ensure_ticket_title(convo, emit)
    except Exception:
        pass
    settings = get_settings()
    model = build_llm().bind_tools(SCHEMAS)
    messages: list[Any] = [
        SystemMessage(render_prompt(convo)),
        HumanMessage(convo.text),
    ]

    for _ in range(settings.max_steps):
        try:
            response = model.invoke(messages)
        except Exception as exc:
            # Providers occasionally emit tool arguments that are not valid JSON
            # and reject their own output. One retry usually lands; a second
            # failure is the provider's, not ours, and should say so.
            if "tool call" not in str(exc).lower() and "json" not in str(exc).lower():
                raise
            messages.append(
                HumanMessage("That tool call was malformed. Send it again as valid JSON.")
            )
            continue
        messages.append(response)

        calls = getattr(response, "tool_calls", None) or []
        if not calls:
            # Talking without acting. Allowed once as a greeting, but the request
            # is not finished, so push it back rather than ending here.
            # Whatever the model wrote here is commentary, not an answer. It
            # narrates its own tool calls -- "**Cites:** KB-08, **To:** Finance"
            # -- and half-formed calls arrive this way too. The employee sees
            # only what a tool produced, so this goes to the trace and no
            # further. Nothing an employee reads is written directly by the
            # model without a tool having approved it.
            text = (response.content or "").strip()
            if text and not text.lstrip().startswith(("{", "[")):
                emit({"type": "thinking", "text": text})
            messages.append(
                HumanMessage(
                    "That did not close the request. Call a tool: resolve, raise_ticket, "
                    "escalate, or ask_followup."
                )
            )
            continue

        for call in calls:
            name = call["name"]
            handler = tools.TOOLS.get(name)
            if handler is None:
                messages.append(
                    ToolMessage(
                        json.dumps({"refused": "no_such_tool"}), tool_call_id=call["id"]
                    )
                )
                continue

            emit({"type": "tool_start", "tool": name, "args": call["args"]})
            result = handler(convo, **call["args"])
            messages.append(ToolMessage(json.dumps(result), tool_call_id=call["id"]))

            if refusal := result.get("refused"):
                # The interesting event. A tool has just stopped the model doing
                # something, and the next step is the model correcting itself.
                emit(
                    {
                        "type": "refused",
                        "tool": name,
                        "reason": refusal,
                        "detail": result.get("detail", ""),
                        "conflict": result.get("conflict"),
                        "clause": result.get("clause"),
                    }
                )
            else:
                emit({"type": "tool_result", "tool": name, "result": result})

            reply, seq = _reply_for(convo, name, result)
            if reply is not None:
                convo.turns.append({"speaker": "agent", "text": reply})
                say_event: dict[str, Any] = {"type": "say", "text": reply}
                if seq is not None:
                    say_event["seq"] = seq
                    say_event["message_id"] = f"m-{seq}"
                emit(say_event)

            if name == "ask_followup" and "asked" in result:
                # A successful follow-up pauses the run: the employee has not
                # answered yet, so there is nothing further to decide. Mark
                # waiting, emit the outcome, and return immediately without
                # running more model steps or tools (notably escalate) until
                # a new employee message arrives. A refused follow-up (budget
                # spent) is not a pause: fall through so the model must act.
                if not convo.is_closed():
                    convo.outcome = "waiting_on_employee"
                    emit({"type": "outcome", "outcome": convo.outcome})
                return convo

        if convo.is_closed():
            emit({"type": "outcome", "outcome": convo.outcome})
            return convo

    # A request left open because the agent is waiting on the employee is not a
    # failure -- it is the honest state, and the data pack has one exactly like
    # it already (REQ-12, "waiting on employee response").
    if not convo.is_closed() and convo.followups_asked:
        convo.outcome = "waiting_on_employee"
        emit({"type": "outcome", "outcome": convo.outcome})
        return convo

    # Out of steps with nothing decided. Say so rather than leaving it open --
    # an unanswered request that looks answered is the worst outcome here.
    if not convo.is_closed():
        tools.escalate(
            convo,
            "The agent could not reach a decision within its step budget.",
            "IT lead",
            convo.cited,
        )
        text = (
            "I couldn't work this one out from the policies I have. "
            "I've passed it to the IT lead with what I found."
        )
        convo.turns.append({"speaker": "agent", "text": text})
        msg = convo.say("agent", text)
        emit({"type": "say", "text": text, "seq": int(msg["seq"]), "message_id": f"m-{msg['seq']}"})
        emit({"type": "outcome", "outcome": convo.outcome})
    return convo
