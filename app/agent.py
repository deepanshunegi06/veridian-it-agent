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
                "category": {"type": "string", "description": "hardware, access, software, network, email, security"},
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
                "to": {"type": "string", "description": "Who: Finance, Security, IT Security, a manager, IT lead."},
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


def _reply_for(convo: Conversation, name: str, result: dict) -> str | None:
    """What the employee sees, when a tool produced something they should read."""
    if name == "ask_followup" and "asked" in result:
        return result["asked"]
    if name == "resolve" and result.get("resolved"):
        return convo.actions[-1]["answer"]
    if name == "escalate":
        return (
            f"I can't decide this one, so I've passed it to {result['escalated_to']} "
            f"with the relevant policy. {result['reason']}"
        )
    if name == "raise_ticket" and "ticket" in result:
        return (
            f"I've raised {result['ticket']} ({result['priority']} priority) and IT will "
            "pick it up from there."
        )
    return None


def run(convo: Conversation) -> Conversation:
    """Work the request until it is closed or the step budget runs out."""
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
            text = (response.content or "").strip()
            # A half-formed tool call sometimes arrives as content. It is not
            # something an employee should ever be shown.
            if text and not text.lstrip().startswith(("{", "[")):
                convo.turns.append({"speaker": "agent", "text": text})
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

            result = handler(convo, **call["args"])
            messages.append(ToolMessage(json.dumps(result), tool_call_id=call["id"]))

            if reply := _reply_for(convo, name, result):
                convo.turns.append({"speaker": "agent", "text": reply})

        if convo.is_closed():
            return convo

    # A request left open because the agent is waiting on the employee is not a
    # failure -- it is the honest state, and the data pack has one exactly like
    # it already (REQ-12, "waiting on employee response").
    if not convo.is_closed() and convo.followups_asked:
        convo.outcome = "waiting_on_employee"
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
        convo.turns.append(
            {
                "speaker": "agent",
                "text": "I couldn't work this one out from the policies I have. "
                "I've passed it to the IT lead with what I found.",
            }
        )
    return convo
