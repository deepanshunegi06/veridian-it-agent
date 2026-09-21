# Veridian IT Desk

An internal IT service desk agent that cites the clause it answered from, and
refuses to answer when the policy does not let it.

Built for the AIONOS Agentic AI Factory assessment, Assignment 2 (Internal
Service Agent). Author: Deepanshu Negi. Time budget: 6 hours.

## The claim

The product's guarantees are functions that return errors, not instructions in a
prompt. `resolve()` in [`app/tools.py`](app/tools.py) rejects an answer with no
citation, an answer citing a clause that does not exist, an answer citing
sources that contradict each other, an answer stating a figure none of the cited
clauses contain, and an answer given for a decision that is not IT's to make.
A model having a bad day can ignore "always cite your source". It cannot ignore
a function that returns `{"refused": "no_citation"}`.

The refusal is not the end of the turn. It goes back to the model as a tool
result with the reason and the material, and the model corrects itself on the
next step. That correction loop is the thing worth watching in the demo.

## Run it

Needs Python 3.12 and a Groq API key (the free tier is enough).

```bash
cp env.example .env        # then put your key in GROQ_API_KEY
uv run uvicorn app.main:app --reload
```

That serves the API on `http://localhost:8000`. `http://localhost:8000/docs`
is the interactive OpenAPI page, which is enough to drive the whole agent
without a frontend.

Without `uv`:

```bash
python -m venv .venv && .venv/bin/pip install -e . && .venv/bin/uvicorn app.main:app --reload
```

The default model is `openai/gpt-oss-120b` on Groq. Gemini and OpenAI work too
by changing `LLM_PROVIDER` and `LLM_MODEL` in `.env`; see
[`app/llm.py`](app/llm.py) and [`docs/ai-tools.md`](docs/ai-tools.md).

**Deployed link: _not deployed yet — URL goes here._**

## What to try first

Every request below is verbatim from the assignment data pack
([`data/requests.yaml`](data/requests.yaml)). Run one with
`POST /requests/REQ-01/run`, which streams the agent's work as server-sent
events, then read `GET /requests/REQ-01` for the audit trail.

| Request | What the employee wrote | What it demonstrates |
| --- | --- | --- |
| REQ-01 | Laptop dead, "had it about 3.5 years now" | KB-03 says three years, ASSET-01 says four. Both are retrieved, `resolve` refuses with `conflicting_sources`, and the agent escalates showing both clauses. The conflict is declared in the data, so it fires whether or not the model notices it. |
| REQ-08 | Phishing email, "forwarding it to a few teammates to check" | The unsafe behaviour is addressed before the question is. KB-09 says such mail must not be forwarded, and its authority is Security, so IT cannot close this itself. |
| REQ-10 | Admin access to the finance reporting server, "urgently" | TK-1050 in the ticket queue was rejected for want of a business justification. `find_policy` returns that precedent, so the same question gets asked before another request is raised. Urgency is not a justification. |
| REQ-12 | Can't log into the expense tool | KB-08 says expense access is Finance's, not IT's. `resolve` refuses with `not_our_authority`, and refuses again with `ignored_the_owner` if the agent tries to answer from the password clause instead. |
| REQ-15 | "hey can you help, its not working" | There is nothing here to look up. The agent asks what is broken rather than guessing a system. Follow-ups are budgeted at two, so it cannot interrogate instead of deciding. |

`POST /chat` takes anything you type and runs the same agent, same tools, same
refusals. `POST /reset` clears the session mid-demo.

On a full pass over all fifteen requests, six resolved, eight escalated or
routed to another function, one ticket raised. That split is the point: most of
an IT desk's work is knowing which of these it is. The exact numbers depend on
the model, so treat them as an observed run rather than a fixed property.

## Architecture in brief

```
HTTP (app/main.py)        streams every step as it happens
  -> loop (app/agent.py)  model picks a tool, refusals come back as material
    -> tools (app/tools.py)  the rules, as functions that return errors
      -> kb (app/kb.py)      the only door to policy text
        -> data/*.yaml       the supplied pack, transcribed
```

Full version, with the request flow diagram and why the layers are split:
[`docs/architecture.md`](docs/architecture.md).

## Documents

- [`docs/architecture.md`](docs/architecture.md) — the system and the process flow
- [`docs/assumptions.md`](docs/assumptions.md) — what was supplied, what was inferred, what it cannot do
- [`docs/ai-tools.md`](docs/ai-tools.md) — the AI tools used and what each was used for
- [`docs/process-flow.md`](docs/process-flow.md) — the simple dashboard process-flow diagram
- [`docs/submission-pack.md`](docs/submission-pack.md) — architecture, inputs, sources, assumptions and AI-tool summary
- [`docs/demo-script.md`](docs/demo-script.md) — timed 15-minute demo, backup prompts and defence questions

## What it does not do

It does not send email or touch a real external ticketing system. The dashboard
does include demo JWT authentication, employee/IT/admin RBAC, MongoDB-backed
conversation persistence, and a human handoff workflow. Its tickets are still
application tickets rather than ServiceNow/Jira records. Retrieval is keyword
scoring over eleven clauses, which is right for eleven clauses and wrong for a
thousand. The limitations are listed in [`docs/submission-pack.md`](docs/submission-pack.md)
and [`docs/assumptions.md`](docs/assumptions.md).
