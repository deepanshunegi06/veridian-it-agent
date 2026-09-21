# Assignment 2 submission pack

This document is the short written companion for the working Veridian IT Desk
prototype.

## 1. What was delivered

Veridian IT Desk is an authenticated internal IT support dashboard with three
roles:

- **Employee:** starts a ticket, chats with the AI agent, sees the ticket title,
  status, policy-safe response, and live IT handoff.
- **IT support:** sees one operational inbox, filters tickets, joins a thread,
  replies as a human, and resolves it.
- **Admin:** manages the knowledge base and support users in addition to the IT
  workflow.

The product demonstrates the complete support loop:

```text
employee message -> ticket + title -> policy retrieval -> guarded decision
-> answer / follow-up / IT handoff -> human reply -> resolution
```

## 2. Architecture and process flow

The simple diagram is in [`process-flow.md`](process-flow.md).

### Runtime architecture

```text
Next.js dashboard
  -> FastAPI authenticated API
      -> JWT/cookie authentication and RBAC
      -> Conversation + ticket state
      -> SSE streams for AI and human updates
      -> Agent loop using MiMo-V2.5 through OpenCode Go
          -> find_policy
          -> ask_followup
          -> resolve
          -> raise_ticket
          -> escalate
              -> policy guardrails in Python
              -> knowledge base retrieval
                  -> data/knowledge_base.yaml
                  -> data/tickets.yaml precedents
      -> MongoDB Atlas persistence
```

### Why the agent is safe to demonstrate

The model chooses the next tool and supplies tool arguments, but it does not
directly write an answer into the system. The Python tools validate:

- citations exist;
- cited policy is not contradictory for the case;
- the answer does not invent unsupported numbers;
- another department is not represented as IT authority;
- tickets are not raised when policy says self-service is enough;
- follow-ups are bounded;
- escalated or human-assigned threads do not restart the AI.

## 3. Inputs, sources, and assumptions

### Inputs to one agent turn

1. Authenticated employee identity and role.
2. The employee's current message.
3. Existing conversation state and ticket state.
4. The system prompt and available tool schemas.
5. Clauses returned by `find_policy`.
6. Relevant closed-ticket precedents.
7. Refusal material returned by guardrail tools.

### Sources used

| Source | Used for |
| --- | --- |
| `backend/data/knowledge_base.yaml` | Eleven policy clauses, authorities, keywords and declared conflicts |
| `backend/data/requests.yaml` | Assignment request examples and seeded employee scenarios |
| `backend/data/tickets.yaml` | Existing ticket examples and precedent decisions |
| MongoDB Atlas | Users, conversations, tickets, events, counters, KB overrides |
| Employee messages | The actual issue, ownership grounding and conversation history |
| IT human messages | Human-in-the-loop replies and resolution events |
| OpenCode Go / MiMo-V2.5 | Runtime language-model inference and tool selection |

### Key assumptions

- The supplied YAML data is the authoritative demo policy pack.
- A new employee conversation receives a ticket before the first AI response.
- Ticket naming is generated once from the first employee message and is not
  regenerated on every follow-up.
- The model may choose actions, but Python guardrails decide whether an action
  is accepted.
- A policy conflict is escalated when it is materially ambiguous; for example,
  the three-year versus four-year laptop rule is ambiguous between three and
  four years, but a clearly four-and-a-half-year-old laptop satisfies both
  thresholds.
- A handoff to IT is a workflow state, not merely an AI answer. Once IT joins,
  employee messages do not invoke the AI again.
- Demo authentication uses seeded development users. Production deployment
  would require operational identity management, secret rotation, and stronger
  deployment controls.
- MongoDB Atlas is the persistence path when `MONGODB_URI` is configured. Tests
  and offline development use an in-memory fallback.

### Known limitations

- Retrieval is keyword scoring over a small knowledge base, not embeddings or a
  large enterprise search index.
- No real email is sent.
- No external ITSM/ticketing platform is called; ticket state is application
  state.
- This is a single-tenant demonstration rather than a production SaaS design.
- The seeded passwords are development-only demo credentials.

## 4. AI tools and how they were used

### Development-time tools

| Tool/model | How it was used |
| --- | --- |
| OpenCode | Development environment and orchestration layer for repository work, testing, service startup and integration checks |
| OpenAI GPT-5.6 Luna | Primary coding/reasoning assistant for architecture, implementation, debugging, documentation and review tasks |
| Meta Muse Spark 1.3 | Additional coding/review support for focused implementation and UI/product reasoning tasks |
| Subagent-driven development | Independent, file-scoped agents implemented backend auth/RBAC, frontend dashboard surfaces, SSE fixes, ticket lifecycle, styling and QA in parallel; the main agent integrated and verified the result |

### Runtime AI

| Tool/model | How it was used |
| --- | --- |
| OpenCode Go | OpenAI-compatible inference API used by the FastAPI runtime |
| Xiaomi MiMo-V2.5 | Agent inference model. It selects tools, requests policy searches, asks follow-ups, proposes cited answers, raises tickets or escalates |
| LangChain tool binding | Connects MiMo-V2.5 to the typed Python tools in `backend/app/agent.py` and `backend/app/tools.py` |

### Runtime sequence

```text
Employee message
  -> MiMo receives system prompt + tool schemas
  -> MiMo chooses a tool
  -> Python guardrail executes the tool
  -> result or refusal returns to MiMo
  -> MiMo corrects or continues
  -> employee receives only the approved response
```

The model is not trusted as the policy authority. It is the planner; the
guardrail functions are the enforcement layer.

## 5. Evidence for the submission

- Working UI: `http://localhost:3000/login`
- API docs: `http://localhost:8000/docs`
- Backend tests: the model-free/auth/ticket regression suite in `tests/`
- Frontend checks: lint, unit tests and production build
- Architecture diagram: [`process-flow.md`](process-flow.md)
- Timed demo: [`demo-script.md`](demo-script.md)
