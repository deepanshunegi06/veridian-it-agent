"""Regression: escalation shows a concise handoff, audit keeps the detail.

The internal escalate ``reason`` (policy clauses, conflict analysis) must stay
in ``convo.actions`` for IT support, while the employee-visible message (and
the ``say`` SSE event) contains only a concise handoff with no KB ids or
reason text.
"""

from __future__ import annotations

from app import agent as agent_mod
from app import tools as tools_mod
from app.tools import Conversation


def _convo(**kwargs) -> Conversation:
    kwargs.setdefault("request_id", "T-ESCALATE")
    kwargs.setdefault("employee", "Aditi")
    kwargs.setdefault("text", "my laptop is dead, about 3.5 years old")
    return Conversation(**kwargs)


DETAILED_REASON = (
    "KB-03 says eligible after 3 years but ASSET-01 says 4 years; "
    "conflicting_sources decision analysis: cannot resolve, needs Finance approval."
)


def test_reply_for_escalate_is_concise_and_keeps_audit_detail():
    convo = _convo()
    result = tools_mod.escalate(convo, DETAILED_REASON, "Finance", ["KB-03", "ASSET-01"])

    # Tool result/action schema and outcome unchanged.
    assert result["escalated_to"] == "Finance"
    assert result["reason"] == DETAILED_REASON
    assert convo.outcome == "escalated"
    stored = convo.actions[-1]
    assert stored["tool"] == "escalate"
    assert stored["reason"] == DETAILED_REASON
    assert stored["to"] == "Finance"
    assert stored["cites"] == ["KB-03", "ASSET-01"]

    reply, seq = agent_mod._reply_for(convo, "escalate", result)

    assert reply == "I've passed this to the IT team. They'll review it and follow up here."
    assert convo.messages[-1]["text"] == reply
    assert convo.messages[-1]["speaker"] == "agent"
    assert seq == convo.messages[-1]["seq"]
    # No internal reasoning leaks to the employee.
    assert "KB-03" not in reply
    assert "ASSET-01" not in reply
    assert DETAILED_REASON not in reply
    assert "Finance" not in reply
    assert "conflict" not in reply.lower()


def test_reply_for_escalate_with_ticket_includes_number_not_reason():
    convo = _convo()
    convo.ticket_id = "TK-1042"
    result = tools_mod.escalate(convo, DETAILED_REASON, "IT lead", ["KB-03"])

    reply, _seq = agent_mod._reply_for(convo, "escalate", result)

    assert "TK-1042" in reply
    assert "KB-03" not in reply
    assert DETAILED_REASON not in reply
    assert convo.actions[-1]["reason"] == DETAILED_REASON
    assert convo.messages[-1]["text"] == reply


class _FakeResponse:
    def __init__(self, tool_calls, content=""):
        self.tool_calls = tool_calls
        self.content = content


class _FakeModel:
    def __init__(self, script):
        self.script = [list(batch) for batch in script]

    def bind_tools(self, _schemas):
        return self

    def invoke(self, _messages):
        batch = self.script.pop(0) if self.script else []
        return _FakeResponse(batch)


def test_agent_run_escalation_say_event_is_concise(monkeypatch):
    script = [
        [
            {
                "name": "escalate",
                "args": {"reason": DETAILED_REASON, "to": "Finance", "cites": ["KB-03"]},
                "id": "c1",
            }
        ],
    ]
    monkeypatch.setattr(agent_mod, "build_llm", lambda *a, **k: _FakeModel(script))

    convo = _convo()
    events: list[dict] = []
    agent_mod.run(convo, on_event=events.append)

    assert convo.outcome == "escalated"
    assert convo.actions[-1]["reason"] == DETAILED_REASON

    says = [e for e in events if e.get("type") == "say"]
    assert len(says) == 1
    assert says[0]["text"] == "I've passed this to the IT team. They'll review it and follow up here."
    assert "KB-03" not in says[0]["text"]
    assert DETAILED_REASON not in says[0]["text"]

    agent_msgs = [m for m in convo.messages if m["speaker"] == "agent"]
    assert len(agent_msgs) == 1
    assert agent_msgs[0]["text"] == says[0]["text"]
    assert convo.turns == [{"speaker": "agent", "text": says[0]["text"]}]
