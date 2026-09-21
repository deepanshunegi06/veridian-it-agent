# Assumptions

> **Current submission note:** this file began as the assignment-agent
> assumptions log. The current authenticated dashboard, ticket lifecycle,
> MongoDB persistence, and human handoff summary are captured in
> [`submission-pack.md`](submission-pack.md). Where this legacy log and the
> current code differ, the current code and submission pack are authoritative.

## Supplied

- Assignment data pack, transcribed into `data/` without rewording policy text:
  - `data/requests.yaml`: 15 employee requests, REQ-01 to REQ-15, with `employee`, `email`, `opened`, `text`, `initial_action`.
  - `data/tickets.yaml`: 10 existing tickets, TK-1042 to TK-1051, with `summary`, `status`, `open`.
  - `data/knowledge_base.yaml`: 11 clauses, KB-01 to KB-10 plus ASSET-01, each with `id`, `title`, `source`, `authority`, `text`, `keywords`.
- The 3-year (KB-03) vs 4-year (ASSET-01) laptop rule contradiction was in the supplied material. Nothing in the pack states which wins.
- The pack notes closed tickets may serve as precedent or prior-resolution context.

## Inferred

- `conflicts_with: [ASSET-01]` on KB-03 and reverse on ASSET-01 is an encoding decision, not supplied text. Conflicts are declared in data and enforced in `kb.conflicts_among`, not inferred at runtime.
- `precedent` field on TK-1043 (3.2-year laptop approved under KB-03 practice), TK-1045 (quota approved at 35GB within KB-06 cap), TK-1050 (admin access rejected for no business justification) is inferred. Other tickets have no `precedent` and are ignored by `kb.precedents`.
- `authority` per clause (e.g. KB-08 Finance, KB-09 Security, KB-10 Manager and Finance) is transcribed as the owner; routing behaviour built on it (`not_our_authority`, `ignored_the_owner`) is inferred.
- `Conversation.best_match` is computed once from the employee text via `kb.search(text, limit=1)`. This grounds the ownership check in employee words rather than the agent query.
- `MAX_FOLLOWUPS = 2` in `app/tools.py` and `max_steps = 10` in `app/llm.py` are chosen budgets, not supplied. `temperature = 0.1` is chosen for determinism.
- Retrieval weights in `kb.search` (keyword hit 3, title word 2, text word 0.25, `limit = 3`) are chosen for 11 short clauses.
- New tickets are numbered TK-1052 and up. The pack does not specify a numbering scheme.

## Limitations

- No email. REQ-08 only advises and routes; nothing sends or forwards mail.
- No real ticketing. `raise_ticket` persists to `app/db.py` (Mongo `conversations`/`events`, in-memory fallback) with a global `TK-1052+` counter. No external system is called.
- The current dashboard uses JWT authentication with an HttpOnly cookie plus a
  Bearer fallback, and server-side roles (`employee`, `it_agent`, `admin`). Old
  fixture/demo routes remain legacy compatibility surfaces and should not be
  used as the product demo.
- Keyword retrieval only. `kb.search` is keyword plus word overlap over 11 clauses. Correct at this size; a larger base would need embeddings or ranking.
- No date math. `opened` is stored and displayed as a string. The prompt states today is 2026-09-25, but no code computes ages, deadlines, or expiry from dates. Thresholds are checked as cited text only.
- Refusals are persisted. `tools._refuse` logs every refusal into `Conversation.actions` without closing the request, plus every SSE event (including `refused`) is stored via `db.log_event`. `GET /requests/{id}` and `/stats` now include them.
- Outcome counts are model-dependent. README reports one observed full pass (six resolved, eight escalated or routed, one ticket raised). Different providers or runs produce different splits.

## State

Session state is `ConversationStore` in `app/main.py`, backed by `app/db.py`.

- Mongo when `MONGODB_URI` is configured in the process or `.env` (DB
  `VERIDIAN_DB`, default `veridian_it_agent`), with an in-memory fallback so
  tests run with no network/key.
- Collections include `conversations`, `events`, `users`, `kb_overrides`, and
  `counters` (`ticket_seq` for global IDs). `/reset` is admin-only and clears
  conversations + events but keeps the ticket counter so IDs stay unique.
- Single process only. No locking beyond Mongo atomic `findOneAndUpdate` for ticket IDs.

## Known rough edges

- `unsupported_figure` misses hyphenated forms. `_NUMBERS` in `app/tools.py` matches `<number><space><unit>` (e.g. `24 hours`) but not `4-year`, which is how ASSET-01 phrases the refresh cycle. A figure check against that clause can false-positive or pass vacuously depending on how the answer phrases it.
- Cited-set over-blocking. `resolve` checks `conflicts_among({*cites, *convo.cited})`, where `convo.cited` accumulates every clause returned by `find_policy` in that conversation. Once a conflicting pair has been retrieved, any later `resolve` in that conversation is refused, even one that cites only one side. This is intentional (prevents dodging a conflict by citing one side) at the cost of blocking legitimate narrowing.
- Ticket IDs are globally unique via `db.next_ticket_id()` (Mongo `counters.ticket_seq`, in-memory fallback). The old per-conversation `TK-1052` collision is fixed.
