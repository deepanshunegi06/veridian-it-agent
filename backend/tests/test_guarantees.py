"""The promises, tested without a model in the room.

Everything here runs against the tools directly. No LLM, no network, no key --
which is the point: if a guarantee only holds when the model cooperates, it is
not a guarantee. These tests are what let the README claim that the agent cannot
invent a policy, rather than that it usually does not.
"""

from __future__ import annotations

import pytest

from app import kb, tools
from app.tools import Conversation


def convo(text: str = "my laptop is dead", employee: str = "Aditi") -> Conversation:
    return Conversation(request_id="T-01", employee=employee, text=text)


# --- the knowledge base ---------------------------------------------------------


def test_the_data_pack_loaded_whole():
    assert len(kb.REQUESTS) == 15
    assert len(kb.TICKETS) == 10
    assert {"KB-01", "KB-10", "ASSET-01"} <= set(kb.CLAUSES)


def test_search_finds_the_rule_not_the_words():
    """An employee's words never match a policy's words. 'My laptop is dead'
    shares no vocabulary with 'eligible for replacement after 3 years'."""
    assert kb.search("my laptop is completely dead")[0].id in {"KB-03", "ASSET-01"}
    assert kb.search("wifi for a visitor tomorrow")[0].id == "KB-07"
    assert kb.search("mailbox is full, can't send")[0].id == "KB-06"


def test_the_conflict_is_declared_both_ways():
    """A conflict found only from one side is a conflict that can be dodged."""
    assert kb.conflicts_among(["KB-03", "ASSET-01"])
    assert kb.conflicts_among(["ASSET-01", "KB-03"])
    assert not kb.conflicts_among(["KB-01", "KB-07"])


# --- what resolve refuses -------------------------------------------------------


def test_an_answer_needs_a_citation():
    assert tools.resolve(convo(), "Sure, I'll sort that out.", [])["refused"] == "no_citation"


def test_a_citation_has_to_exist():
    out = tools.resolve(convo(), "Policy KB-99 covers this.", ["KB-99"])
    assert out["refused"] == "unknown_clause"


def test_contradicting_sources_cannot_be_resolved():
    c = convo()
    tools.find_policy(c, "laptop replacement 3.5 years old dead")
    for cites in (["KB-03"], ["ASSET-01"], ["KB-03", "ASSET-01"]):
        out = tools.resolve(c, "You are eligible for a replacement.", cites)
        assert out["refused"] == "conflicting_sources", f"{cites} slipped through"


def test_a_figure_not_in_the_cited_clause_is_refused():
    """The specific way a model invents policy is to keep the shape of the rule
    and change the number in it."""
    c = convo("guest wifi for a visitor")
    tools.find_policy(c, "guest wifi visitor")
    out = tools.resolve(c, "Guest credentials last 48 hours.", ["KB-07"])
    assert out["refused"] == "unsupported_figure"
    assert "48 hour" in out["detail"]

    # The figure the clause actually gives is fine.
    ok = tools.resolve(c, "Guest credentials are valid for 24 hours.", ["KB-07"])
    assert ok.get("resolved") is True


def test_another_department_s_work_is_not_answered_as_it():
    c = convo("I can't log into the expense tool")
    tools.find_policy(c, "expense tool login")
    out = tools.resolve(c, "I've restored your access.", ["KB-08"])
    assert out["refused"] == "not_our_authority"
    assert "Finance" in out["detail"]


def test_the_owner_cannot_be_sidestepped_by_citing_something_else():
    """A model that wants a particular answer writes the query that finds it.
    Asked about the expense tool it searched for password-reset policy, floated
    the password clause to the top and answered from that. The ownership check is
    grounded in the employee's words, which the model does not write."""
    c = convo("I can't log into the expense tool, keeps saying invalid credentials")
    assert c.best_match == "KB-08"
    tools.find_policy(c, "expense tool login invalid credentials password reset policy")
    out = tools.resolve(c, "Reset your password on the self-service portal.", ["KB-01"])
    assert out["refused"] == "ignored_the_owner"
    assert "Finance" in out["detail"]


# --- the other tools ------------------------------------------------------------


def test_no_ticket_is_raised_for_something_self_service():
    """The fastest service desk is the one that does not open work it needn't."""
    c = convo("guest wifi for a visitor")
    tools.find_policy(c, "guest wifi visitor")
    out = tools.raise_ticket(c, "network", "Guest wifi for a visitor", "normal")
    assert out["refused"] == "no_ticket_needed"
    assert "KB-07" in out["detail"]


def test_followups_are_budgeted():
    c = convo("its not working")
    assert "asked" in tools.ask_followup(c, "Which system?", "cannot tell which rule applies")
    assert "asked" in tools.ask_followup(c, "On a company device?", "narrows it down")
    spent = tools.ask_followup(c, "And when did it start?", "still narrowing")
    assert spent["refused"] == "followup_budget_spent"


def test_escalation_is_never_refused_and_carries_its_sources():
    """Escalating unnecessarily costs a minute of someone's time. Not escalating
    when it was necessary costs an approval nobody authorised."""
    c = convo()
    tools.find_policy(c, "laptop replacement 3.5 years")
    out = tools.escalate(c, "The policies disagree.", "Finance", ["KB-03", "ASSET-01"])
    assert c.outcome == "escalated"
    assert {s["id"] for s in out["sources"]} == {"KB-03", "ASSET-01"}
    assert all(s["text"] for s in out["sources"])


# --- the audit trail ------------------------------------------------------------


def test_every_tool_call_is_recorded_in_order():
    c = convo("guest wifi for a visitor")
    tools.find_policy(c, "guest wifi")
    tools.resolve(c, "Generate one at the front-desk kiosk.", ["KB-07"])
    assert [a["tool"] for a in c.actions] == ["find_policy", "resolve"]
    assert all(a["at"] for a in c.actions)
    assert c.outcome == "resolved"


def test_a_refused_call_does_not_close_the_request():
    """A refusal is a correction, not an outcome. The request stays open."""
    c = convo()
    tools.find_policy(c, "laptop replacement 3.5 years")
    tools.resolve(c, "You're eligible.", ["KB-03"])
    assert c.outcome is None
    assert not c.is_closed()


@pytest.mark.parametrize(
    ("request_id", "expected_owner"),
    [("REQ-12", "Finance"), ("REQ-08", "Security"), ("REQ-07", "Manager and Finance")],
)
def test_requests_that_belong_to_another_function_are_recognised(request_id, expected_owner):
    row = next(r for r in kb.REQUESTS if r["id"] == request_id)
    c = Conversation(request_id=row["id"], employee=row["employee"], text=row["text"])
    assert kb.clause(c.best_match).authority == expected_owner


# --- grounded KB-03 / ASSET-01 material conflict --------------------------------


def _laptop_convo(age_text: str) -> Conversation:
    c = convo(age_text)
    tools.find_policy(c, f"laptop replacement {age_text}")
    return c


def test_laptop_conflict_resolves_when_age_clearly_over_four():
    c = _laptop_convo("My laptop won't turn on at all, it's completely dead, had it about 4.5 years now.")
    out = tools.resolve(c, "You are eligible for a replacement.", ["KB-03", "ASSET-01"])
    assert out.get("resolved") is True
    assert c.outcome == "resolved"
    assert c.ticket_status == "resolved"
    assert getattr(c, "resolution_source", None) == "ai"
    assert [a["tool"] for a in c.actions] == ["find_policy", "resolve"]
    assert all(a["at"] for a in c.actions)


def test_laptop_conflict_still_refuses_between_three_and_four():
    c = _laptop_convo("My laptop won't turn on at all, it's completely dead, had it about 3.5 years now.")
    for cites in (["KB-03"], ["ASSET-01"], ["KB-03", "ASSET-01"]):
        out = tools.resolve(c, "You are eligible for a replacement.", cites)
        assert out["refused"] == "conflicting_sources", f"{cites} slipped through"
    assert c.outcome is None
    assert not c.is_closed()


def test_laptop_conflict_still_refuses_when_age_missing():
    c = _laptop_convo("my laptop is dead")
    for cites in (["KB-03"], ["ASSET-01"], ["KB-03", "ASSET-01"]):
        out = tools.resolve(c, "You are eligible for a replacement.", cites)
        assert out["refused"] == "conflicting_sources", f"{cites} slipped through"
    assert c.outcome is None


def test_verified_hardware_failure_does_not_bypass_finance_between_three_and_four():
    c = _laptop_convo(
        "My laptop had a verified hardware failure, it is 3.5 years old and completely dead."
    )
    out = tools.resolve(c, "You are eligible for a replacement.", ["KB-03", "ASSET-01"])
    assert out["refused"] == "conflicting_sources"
    assert c.outcome is None


def test_unsupported_figure_still_refused_even_when_age_over_four():
    c = _laptop_convo("My laptop is dead, had it about 4.5 years now.")
    out = tools.resolve(c, "Laptops are replaced after 4.5 years.", ["KB-03", "ASSET-01"])
    assert out["refused"] == "unsupported_figure"
    assert c.outcome is None
    ok = tools.resolve(c, "You are eligible for a replacement.", ["KB-03", "ASSET-01"])
    assert ok.get("resolved") is True


def test_followup_age_answer_can_ground_resolve_over_four():
    c = convo("my laptop is dead")
    tools.find_policy(c, "laptop replacement dead")
    c.say("user", "It is 4.5 years old.", name="Aditi")
    c.text = "It is 4.5 years old."
    out = tools.resolve(c, "You are eligible for a replacement.", ["KB-03", "ASSET-01"])
    assert out.get("resolved") is True


def test_contradictory_ages_stay_conservative():
    c = convo("my laptop is dead, had it 3.5 years")
    tools.find_policy(c, "laptop replacement dead")
    c.say("user", "Actually it is 4.5 years old.", name="Aditi")
    c.text = "Actually it is 4.5 years old."
    out = tools.resolve(c, "You are eligible for a replacement.", ["KB-03", "ASSET-01"])
    assert out["refused"] == "conflicting_sources"
    assert c.outcome is None
