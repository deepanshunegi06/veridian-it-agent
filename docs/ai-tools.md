# AI tools and their use

## Development-time AI tools

- **OpenCode** — the development environment and orchestration layer used to
  inspect the repository, run commands, start services, and coordinate the
  implementation workflow.
- **OpenAI GPT-5.6 Luna** — primary coding and reasoning assistant used for
  architecture, implementation, debugging, code review, testing and
  documentation.
- **Meta Muse Spark 1.3** — additional focused coding/review support for UI,
  product-flow and implementation tasks.
- **Subagent-driven development** — work was divided into file-scoped parallel
  tasks such as backend auth/RBAC, ticket lifecycle, SSE reliability, employee
  UI, IT inbox UI, light visual styling, documentation and regression testing.
  The main integration process reviewed the changes and ran the test/build
  gates.

No API keys, passwords, MongoDB URIs or private employee data were pasted into
the coding assistants. Runtime secrets are kept in the ignored `.env` file.

## Runtime AI

- **OpenCode Go** — OpenAI-compatible inference endpoint used by the FastAPI
  application.
- **Xiaomi MiMo-V2.5** — runtime agent model. It receives the system prompt,
  employee message, tool schemas and tool results. It decides whether to search
  policy, ask a follow-up, resolve, raise a ticket or escalate.
- **LangChain tool binding** — connects MiMo-V2.5 to the typed Python tools in
  `backend/app/agent.py` and `backend/app/tools.py`.

## Runtime workflow

```text
Employee message
  -> MiMo chooses a tool
  -> Python guardrail executes the tool
  -> result or refusal returns to MiMo
  -> MiMo corrects or continues
  -> only the approved response reaches the employee
```

The model is the planner, not the policy authority. The policy and safety
checks live in Python and are covered by model-free tests.

## Human review

- Refusal branches are tested without a model using the Python test suite.
- The assignment data pack was checked against its 15 requests, 10 tickets and
  11 knowledge-base clauses.
- Live flows were reviewed for password reset, policy conflict, Finance-owned
  expense access, security routing, follow-up questions and human handoff.
- Where generated text and code could disagree, the code and tests are treated
  as authoritative.
