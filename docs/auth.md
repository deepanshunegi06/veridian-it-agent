# Auth (backend foundation)

Roles: `employee`, `it_agent`, `admin`.

## Endpoints

- `POST /auth/login {email, password}` -> `{user, access_token, token_type}`.
  Sets an HttpOnly cookie named `veridian_access` (from `AUTH_COOKIE_NAME`).
- `GET /auth/me` -> `{id, email, name, role, is_active}`.
- `POST /auth/logout` -> `{ok: true}` (clears the cookie).
- `GET /auth/users`, `POST /auth/users`, `PATCH /auth/users/{id}` — admin only.

Tokens are JWT HS256 (PyJWT). Clients may send the token as the cookie or as
`Authorization: Bearer <token>`; when both are present the Bearer header wins
so API tests are never shadowed by a stored cookie. Passwords are hashed with
bcrypt; hashes never appear in responses or logs.

## Threads and ownership

`Conversation.owner_user_id` is set from auth at `POST /threads` time. The
`employee` / `agent_name` fields in request bodies are still accepted so old
clients do not 422, but they are ignored.

- Employees: create threads, list/read/message only their own (`403` otherwise).
- `it_agent` / `admin`: list/read all, `join` / `human` / `resolve`.
- Only `admin` can write KB (`POST /kb`, `PUT /kb/{id}`) and manage users.
- `GET /kb` requires auth (any role). `GET /healthz` is public.
- Legacy demo mutations (`POST /reset`, `POST /requests/{id}/run`, `POST /chat`)
  are admin-only.
- An `it_agent` cannot resolve a thread assigned to a different agent;
  `admin` can. Assignment is tracked by `assigned_to_user_id` (names are
  display only).

`GET /threads` accepts `status` (matches `ticket_status`/`outcome`), `q`
(searches request/employee/text) and `assigned` (agent name, or `unassigned`).
`GET /inbox` is the same filter set over live conversations for support roles.

## Demo seeds (dev only)

On startup (and via `py -m app.seed`) the backend seeds three demo accounts
idempotently — existing emails are never overwritten, passwords never printed:

- `employee@veridian.local` / `Employee-Demo-01!`
- `agent@veridian.local` / `Agent-Demo-01!`
- `admin@veridian.local` / `Admin-Demo-01!`

Seeding only happens when `ENV=dev` and `SEED_DEMO_USERS=true`. Emails and
passwords are env-configurable (`DEMO_*_EMAIL`, `DEMO_*_PASSWORD`). Users
persist in Mongo when `MONGODB_URI` is set, otherwise in memory.

## CORS

Credentials are enabled with explicit origins from `CORS_ORIGINS`. A wildcard
is never used with credentials; empty/`"*"` falls back to
`http://localhost:3000`.
