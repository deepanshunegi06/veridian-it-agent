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
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import agent, kb, tools
from .llm import get_settings
from .tools import Conversation

app = FastAPI(
    title="Veridian IT Desk",
    description="An internal IT service agent that cites what it says and refuses what it cannot decide.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list or ["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In memory on purpose. The data pack reseeds on every boot, so a restart costs
# nothing that matters and the deployment needs no disk at all.
CONVERSATIONS: dict[str, Conversation] = {}


# --- reference data -------------------------------------------------------------


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.get("/kb")
def knowledge_base() -> list[dict]:
    """Every clause, so the UI can show a citation in full without another call."""
    return [
        {**c.cite(), "keywords": c.keywords, "conflicts_with": c.conflicts_with}
        for c in kb.CLAUSES.values()
    ]


@app.get("/requests")
def requests() -> list[dict]:
    """The employee queue from the data pack, with whatever the agent has done."""
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
def tickets() -> list[dict]:
    """The pre-existing queue, plus anything the agent has raised this session."""
    existing = [{**t, "origin": "data pack"} for t in kb.TICKETS]
    raised = [
        {
            "id": action["ticket"],
            "employee": convo.employee,
            "summary": action["summary"],
            "status": f"Open - {action['priority']} priority",
            "open": True,
            "origin": "raised by the agent",
            "request_id": convo.request_id,
            "category": action["category"],
        }
        for convo in CONVERSATIONS.values()
        for action in convo.actions
        if action["tool"] == "raise_ticket" and "ticket" in action
    ]
    return existing + raised


@app.get("/requests/{request_id}")
def one_request(request_id: str) -> dict:
    """Everything that happened to one request. This is the audit trail."""
    row = next((r for r in kb.REQUESTS if r["id"] == request_id), None)
    convo = CONVERSATIONS.get(request_id)
    if row is None and convo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such request")
    return {
        "id": request_id,
        "employee": convo.employee if convo else row["employee"],
        "text": convo.text if convo else row["text"],
        "opened": str(row["opened"]) if row else "",
        "initial_action": row.get("initial_action", "") if row else "",
        "outcome": convo.outcome if convo else None,
        "turns": convo.turns if convo else [],
        "actions": convo.actions if convo else [],
        "cited": convo.cited if convo else [],
    }


# --- running the agent ----------------------------------------------------------


def _event(name: str, data: Any) -> str:
    return f"event: {name}\ndata: {json.dumps(data, default=str)}\n\n"


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
    yield _event("start", {"request_id": convo.request_id, "employee": convo.employee})

    while True:
        event = await queue.get()
        if event is None:
            break
        yield _event(event.pop("type"), event)

    await task
    yield _event(
        "done",
        {
            "outcome": convo.outcome,
            "cited": convo.cited,
            "steps": len(convo.actions),
            "turns": convo.turns,
        },
    )


SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    # Nginx and friends buffer by default, which turns a live trace back into a
    # single late blob. This is the header that stops them.
    "X-Accel-Buffering": "no",
}


@app.post("/requests/{request_id}/run")
async def run_request(request_id: str) -> StreamingResponse:
    """Work one request from the data pack."""
    row = next((r for r in kb.REQUESTS if r["id"] == request_id), None)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such request")

    convo = Conversation(
        request_id=row["id"],
        employee=row["employee"],
        text=row["text"],
        initial_action=row.get("initial_action", ""),
        opened=str(row["opened"]),
    )
    CONVERSATIONS[request_id] = convo
    return StreamingResponse(_stream(convo), media_type="text/event-stream", headers=SSE_HEADERS)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    employee: str = Field("You", max_length=80)


@app.post("/chat")
async def chat(body: ChatRequest) -> StreamingResponse:
    """Anything a visitor types. Same agent, same tools, same refusals."""
    convo = Conversation(
        request_id=f"LIVE-{len(CONVERSATIONS) + 1:03d}",
        employee=body.employee,
        text=body.message,
    )
    CONVERSATIONS[convo.request_id] = convo
    return StreamingResponse(_stream(convo), media_type="text/event-stream", headers=SSE_HEADERS)


@app.get("/stats")
def stats() -> dict:
    """What the agent has done, for the board at the top of the queue."""
    done = list(CONVERSATIONS.values())
    refusals = [
        a for c in done for a in c.actions if a["tool"] == "resolve" and "refused" in a
    ]
    return {
        "handled": len(done),
        "resolved": sum(1 for c in done if c.outcome == "resolved"),
        "escalated": sum(1 for c in done if c.outcome == "escalated"),
        "tickets": sum(1 for c in done if c.outcome == "ticket_raised"),
        "waiting": sum(1 for c in done if c.outcome == "waiting_on_employee"),
        "refusals": len(refusals),
        "total_requests": len(kb.REQUESTS),
    }


@app.post("/reset")
def reset() -> dict:
    """Clear the session. Handy mid-demo, and the data pack is untouched."""
    CONVERSATIONS.clear()
    return {"cleared": True}


# Keep the tools module imported for its side-effect-free registry, and make the
# dependency explicit so a linter does not helpfully remove it.
assert tools.TOOLS
