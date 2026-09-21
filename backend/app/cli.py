"""Work the queue from a terminal.

    python -m app.cli                    every request in the data pack
    python -m app.cli REQ-01 REQ-12      just those
    python -m app.cli --ask "vpn is down"

Exists because a reviewer should be able to see the agent work without starting
two servers, and because watching all fifteen run in one screen is the fastest
way to judge whether the thing is any good.
"""

from __future__ import annotations

import argparse
import sys

from . import kb
from .agent import run
from .tools import Conversation

# What each tool call looks like in the trace. Refusals get their own marker
# because they are the interesting line, not the failure line.
MARK = {
    "find_policy": "search",
    "ask_followup": "ask",
    "resolve": "answer",
    "raise_ticket": "ticket",
    "escalate": "escalate",
}
OUTCOME = {
    "resolved": "RESOLVED",
    "escalated": "ESCALATED",
    "ticket_raised": "TICKET",
    "waiting_on_employee": "WAITING",
}


def _trace(event: dict) -> None:
    kind = event.get("type")
    if kind == "tool_start":
        args = event["args"]
        detail = (
            args.get("query") or args.get("question") or args.get("answer") or args.get("reason") or ""
        )
        print(f"    {MARK.get(event['tool'], event['tool']):9} {str(detail)[:88]}")
    elif kind == "tool_result" and event["tool"] == "find_policy":
        found = [c["id"] for c in event["result"].get("clauses", [])]
        conflict = event["result"].get("conflict")
        print(f"    {'->':9} {', '.join(found) or 'nothing'}")
        if conflict:
            pair = " and ".join(conflict[0]["between"])
            print(f"    {'!':9} {pair} contradict each other")
    elif kind == "refused":
        print(f"    {'REFUSED':9} {event['reason']}: {event['detail'][:80]}")


def one(request_id: str, text: str, employee: str, initial: str = "", opened: str = "") -> bool:
    convo = Conversation(
        request_id=request_id,
        employee=employee,
        text=text,
        initial_action=initial,
        opened=opened,
    )
    print(f"\n{request_id}  {employee}")
    print(f'  "{text}"')
    try:
        run(convo, on_event=_trace)
    except Exception as exc:
        print(f"  ERROR {type(exc).__name__}: {str(exc)[:120]}")
        return False

    for turn in convo.turns:
        print(f"\n  agent: {turn['text']}")
    cited = ", ".join(convo.cited) or "nothing"
    verdict = OUTCOME.get(convo.outcome or "", "OPEN")
    print(f"\n  {verdict}  ({len(convo.actions)} steps, cited {cited})")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(prog="veridian")
    parser.add_argument("requests", nargs="*", help="request ids, e.g. REQ-01. Default: all of them.")
    parser.add_argument("--ask", help="a question of your own, as an employee would write it")
    args = parser.parse_args()

    if args.ask:
        return 0 if one("LIVE-01", args.ask, "You") else 1

    wanted = set(args.requests) or None
    rows = [r for r in kb.REQUESTS if not wanted or r["id"] in wanted]
    if not rows:
        print(f"No such request. Known: {', '.join(r['id'] for r in kb.REQUESTS)}")
        return 1

    failed = sum(
        not one(r["id"], r["text"], r["employee"], r.get("initial_action", ""), str(r["opened"]))
        for r in rows
    )
    print(f"\n{len(rows) - failed}/{len(rows)} requests worked through")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    sys.exit(main())
