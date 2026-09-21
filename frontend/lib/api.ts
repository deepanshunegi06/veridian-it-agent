/* Shared API shapes and helpers for the Veridian IT Desk demo UI.
 * The backend is FastAPI on NEXT_PUBLIC_API_URL (default http://localhost:8000).
 * All pages fetch client-side so `next build` never needs a live backend.
 *
 * Auth: every request below sends `credentials: "include"` (the HttpOnly
 * `veridian_access` cookie is primary) plus a Bearer fallback when a token is
 * available (see `@/lib/auth`). Failures throw ApiError (re-exported here so
 * existing `@/lib/api` imports keep compiling); callers own navigation. */

import { API_BASE, ApiError, getAuthHeaders, setAccessToken } from "./auth";
import { redirectToLoginForExpiredSession } from "./session-expired";

export { ApiError };
export type { Role, User } from "./auth";

export const API_URL = API_BASE;

export interface QueueRow {
  id: string;
  employee: string;
  email?: string;
  text: string;
  opened: string;
  initial_action?: string;
  outcome: string | null;
  steps: number;
  cited: string[];
}

export interface Clause {
  id: string;
  title: string;
  source: string;
  authority: string;
  text: string;
  keywords: string[];
  conflicts_with: string[];
}

export interface Ticket {
  id: string;
  employee?: string;
  summary?: string;
  status: string;
  open: boolean;
  origin: string;
  request_id?: string;
  category?: string;
  precedent?: string;
  assigned_to?: string | null;
  ticket_status?: string | null;
  outcome?: string | null;
}

export interface Stats {
  handled: number;
  resolved: number;
  escalated: number;
  tickets: number;
  waiting: number;
  refusals: number;
  total_requests: number;
}

export interface Turn {
  speaker: string;
  text: string;
}

export type Action = { tool: string } & Record<string, unknown>;

export interface RequestDetail {
  id: string;
  employee: string;
  text: string;
  opened: string;
  initial_action: string;
  outcome: string | null;
  turns: Turn[];
  actions: Action[];
  cited: string[];
}

export interface SseEvent {
  kind: string;
  data: unknown;
}

/** Merge caller headers over the Bearer fallback without dropping it. */
function withAuthHeaders(init?: RequestInit): Record<string, string> {
  return { ...getAuthHeaders(), ...((init?.headers as Record<string, string> | undefined) ?? {}) };
}

/** Paths whose own 401 must never trigger the expired-session redirect:
 * the sign-in attempt (bad credentials stay an inline form error) and the
 * sign-out attempt (already leaving). */
function isAuthExemptPath(path: string): boolean {
  return (
    path === "/auth/login" ||
    path.startsWith("/auth/login/") ||
    path === "/auth/logout" ||
    path.startsWith("/auth/logout/")
  );
}

/** Clear the stale Bearer fallback and navigate once to
 * `/login?next=<current>&expired=1` on 401. No-op for exempt auth paths,
 * during SSR, on `/login` itself, or when a redirect is already in flight.
 * Expected aborts are handled by callers via `isAbortError`; non-401 errors
 * are untouched so they stay visible inline. */
function handleUnauthorized(path: string, status: number): void {
  if (status !== 401) return;
  if (isAuthExemptPath(path)) return;
  setAccessToken(null);
  redirectToLoginForExpiredSession();
}

export async function apiGet<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    cache: "no-store",
    credentials: init?.credentials ?? "include",
    headers: withAuthHeaders(init),
  });
  if (!res.ok) {
    handleUnauthorized(path, res.status);
    throw new ApiError(
      `GET ${path}`,
      res.status,
      await res.text().catch(() => ""),
    );
  }
  return (await res.json()) as T;
}

export async function apiPost<T>(
  path: string,
  body?: unknown,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    method: "POST",
    credentials: init?.credentials ?? "include",
    headers: { "Content-Type": "application/json", ...withAuthHeaders(init) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    handleUnauthorized(path, res.status);
    throw new ApiError(
      `POST ${path}`,
      res.status,
      await res.text().catch(() => ""),
    );
  }
  const text = await res.text();
  return (text ? JSON.parse(text) : null) as T;
}

/** POST to an SSE endpoint and dispatch each event as it arrives. */
export async function streamSSE(
  path: string,
  body: unknown,
  onEvent: (ev: SseEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  if (signal?.aborted) return;
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json", ...getAuthHeaders() },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
  } catch (e) {
    if (isAbortError(e)) return;
    throw e;
  }
  if (!res.ok) {
    handleUnauthorized(path, res.status);
    throw new ApiError(
      `POST ${path}`,
      res.status,
      await res.text().catch(() => ""),
    );
  }
  if (!res.body) throw new Error("Response has no body to stream");
  try {
    await pumpSSE(res.body, onEvent, signal);
  } catch (e) {
    if (isAbortError(e)) return;
    throw e;
  }
}

/** GET an SSE endpoint (e.g. thread live stream) and dispatch each event. */
export async function streamGetSSE(
  path: string,
  onEvent: (ev: SseEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  if (signal?.aborted) return;
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      credentials: "include",
      headers: { Accept: "text/event-stream", ...getAuthHeaders() },
      signal,
    });
  } catch (e) {
    if (isAbortError(e)) return;
    throw e;
  }
  if (!res.ok) {
    handleUnauthorized(path, res.status);
    throw new ApiError(
      `GET ${path}`,
      res.status,
      await res.text().catch(() => ""),
    );
  }
  if (!res.body) throw new Error("Response has no body to stream");
  try {
    await pumpSSE(res.body, onEvent, signal);
  } catch (e) {
    if (isAbortError(e)) return;
    throw e;
  }
}

async function pumpSSE(
  body: ReadableStream<Uint8Array>,
  onEvent: (ev: SseEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buf = "";

  const dispatch = (raw: string) => {
    let name = "";
    const datas: string[] = [];
    for (const line of raw.split("\n")) {
      if (line.startsWith("event:")) name = line.slice(6).trim();
      else if (line.startsWith("data:")) datas.push(line.slice(5).trim());
    }
    if (!name) return;
    const text = datas.join("\n");
    let data: unknown = text;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      /* keep raw text */
    }
    onEvent({ kind: name, data });
  };

  // Proactively settle a pending read when the subscriber tears down.
  // Aborting the fetch usually errors the body stream on its own, but
  // cancelling here guarantees the pending `read()` below settles even on
  // streams where it would otherwise hang (route change, ticket switch,
  // strict-mode remount). The resulting rejection/resolve is swallowed as
  // an expected abort by the loop guard, never surfaced to the UI.
  const onAbort = () => {
    try {
      void reader.cancel(signal?.reason).catch(() => {});
    } catch {
      /* ignore — loop guard handles teardown */
    }
  };
  if (signal?.aborted) {
    onAbort();
    try {
      reader.releaseLock();
    } catch {
      /* ignore */
    }
    return;
  }
  signal?.addEventListener("abort", onAbort);

  try {
    for (;;) {
      if (signal?.aborted) break;
      let read: ReadableStreamReadResult<Uint8Array>;
      try {
        read = await reader.read();
      } catch (e) {
        // Expected teardown (route change, ticket switch, strict-mode
        // remount): the pending read rejects with the abort reason. Break
        // silently so cleanup never surfaces as a console AbortError.
        // Anything else is a real stream failure and still throws.
        if (signal?.aborted || isAbortError(e)) break;
        throw e;
      }
      const { done, value } = read;
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx: number;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const frame = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        dispatch(frame);
      }
    }
    if (buf.trim()) dispatch(buf);
  } finally {
    signal?.removeEventListener("abort", onAbort);
    try {
      reader.releaseLock();
    } catch {
      /* ignore */
    }
  }
}

/** True for expected AbortError/DOMException aborts (strict-mode cleanup,
 * unmount, filter change). Narrow on purpose: only the abort name/code match,
 * so real network errors still surface. */
export function isAbortError(e: unknown): boolean {
  if (!e || typeof e !== "object") return false;
  const err = e as { name?: unknown; code?: unknown };
  if (err.name === "AbortError") return true;
  // DOMException abort is code 20 ("ABORT_ERR").
  if (err.code === 20) return true;
  if (typeof DOMException !== "undefined" && e instanceof DOMException) {
    return e.name === "AbortError" || e.code === 20;
  }
  return false;
}

/** Compact one-line summary of an event payload for the trace view. */
export function eventSummary(ev: SseEvent): string {
  const d = ev.data as Record<string, unknown>;
  switch (ev.kind) {
    case "start":
      return `start ${String(d.request_id ?? "")} (${String(d.employee ?? "")})`;
    case "tool_start":
      return `${String(d.tool ?? "?")} ${JSON.stringify(d.args ?? {})}`;
    case "tool_result":
      return `${String(d.tool ?? "?")} → ${JSON.stringify(d.result ?? d)}`;
    case "refused":
      return `${String(d.tool ?? "?")} refused: ${String(d.reason ?? "")}${d.detail ? ` — ${String(d.detail)}` : ""}`;
    case "thinking":
      return String(d.text ?? "");
    case "say":
      return String(d.text ?? "");
    case "outcome":
      return `outcome: ${String(d.outcome ?? "")}`;
    case "done":
      return `done — outcome=${String(d.outcome ?? "")} steps=${String(d.steps ?? "")} cited=${JSON.stringify(d.cited ?? [])}`;
    case "error":
      return `ERROR: ${String(d.detail ?? "unknown")}`;
    default:
      return JSON.stringify(ev.data);
  }
}

export function pretty(data: unknown): string {
  if (typeof data === "string") return data;
  try {
    return JSON.stringify(data, null, 2);
  } catch {
    return String(data);
  }
}

/* ---- New backend contract: threads, tickets, KB (stable) ---- */

async function requestJson<T>(
  method: string,
  path: string,
  body?: unknown,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    method,
    credentials: init?.credentials ?? "include",
    headers: { "Content-Type": "application/json", ...withAuthHeaders(init) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    handleUnauthorized(path, res.status);
    const text = await res.text().catch(() => "");
    throw new ApiError(`${method} ${path}`, res.status, text);
  }
  const text = await res.text();
  return (text ? JSON.parse(text) : null) as T;
}

export async function apiPut<T>(
  path: string,
  body?: unknown,
  init?: RequestInit,
): Promise<T> {
  return requestJson<T>("PUT", path, body, init);
}

export interface BackendMessage {
  seq: number;
  speaker: string;
  name?: string;
  text: string;
  at?: string;
  /* Forward-compatible author identity: the backend currently sends `name`
   * only, but when an author id is present it is preferred for own-message
   * detection. Optional so older payloads keep working. */
  author_id?: string | null;
  authorId?: string | null;
  user_id?: string | null;
  userId?: string | null;
}

export interface ThreadSummary {
  request_id: string;
  employee: string;
  text: string;
  outcome: string | null;
  assigned_to: string | null;
  /* Who joined. Names are display only; enforcement uses the id. Optional in
   * list payloads (older rows predate it); the UI prefers the id when
   * present and falls back to the display name. */
  assigned_to_user_id?: string | null;
  ticket_status: string | null;
  message_count: number;
  last_message: string | null;
  cited: string[];
  /* Automatic ticket metadata when the backend attaches it. Optional because
   * older threads and list responses may not carry it; the UI falls back to
   * deriving the ticket number from `actions` (raise_ticket) instead. */
  ticket_id?: string | null;
  /* Alias returned by every backend thread payload alongside ticket_id.
   * Older list summaries may carry one and not the other, so helpers check
   * both before falling back to actions. */
  ticket?: string | null;
}

export interface ThreadDetail extends ThreadSummary {
  messages: BackendMessage[];
  turns: Turn[];
  actions: Action[];
  ticket_id?: string | null;
  ticket?: string | null;
}

export interface CreateThreadResponse {
  request_id: string;
  messages: BackendMessage[];
  ticket_id?: string | null;
  ticket?: string | null;
  ticket_status?: string | null;
}

/** Ticket number for a thread: explicit backend fields first, then the
 * `raise_ticket` action audit trail. Null when no ticket exists yet — callers
 * render a graceful "unavailable" state instead of crashing. */
export function threadTicketId(t: {
  ticket_id?: unknown;
  ticket?: unknown;
  actions?: Action[];
} | null | undefined): string | null {
  if (!t) return null;
  if (typeof t.ticket_id === "string" && t.ticket_id) return t.ticket_id;
  if (typeof t.ticket === "string" && t.ticket) return t.ticket;
  const actions = Array.isArray(t.actions) ? t.actions : [];
  for (let i = actions.length - 1; i >= 0; i--) {
    const a = actions[i] as Record<string, unknown>;
    if (a?.tool === "raise_ticket" && typeof a.ticket === "string" && a.ticket) {
      return a.ticket;
    }
  }
  return null;
}

/** Employee ticket fallback for the selected thread.
 *
 * Order: detail ticket (explicit fields, then actions), selected list
 * summary ticket (explicit fields, then actions), known live ticket map for
 * the selected id, live in-flight ticket from the active run, then null.
 * The summary step matters because GET /threads/{id} can race the detail
 * refresh while the list summary already carries ticket_id; the map step
 * matters because a ticket raised over SSE is known before any refresh
 * lands. Null means genuinely unknown — callers render "unavailable". */
export function resolveEmployeeTicketId(opts: {
  detail?: { ticket_id?: unknown; ticket?: unknown; actions?: Action[] } | null;
  summary?: { ticket_id?: unknown; ticket?: unknown; actions?: Action[] } | null;
  ticketMap?: Record<string, string> | null;
  selectedId?: string | null;
  liveTicketId?: string | null;
}): string | null {
  const fromDetail = threadTicketId(opts.detail ?? null);
  if (fromDetail) return fromDetail;
  const fromSummary = threadTicketId(opts.summary ?? null);
  if (fromSummary) return fromSummary;
  const sel = opts.selectedId ?? null;
  if (sel && opts.ticketMap) {
    const mapped = opts.ticketMap[sel];
    if (typeof mapped === "string" && mapped) return mapped;
  }
  if (typeof opts.liveTicketId === "string" && opts.liveTicketId) {
    return opts.liveTicketId;
  }
  return null;
}

/** Employee-facing ticket status derived from ticket lifecycle + outcome.
 * Never infers escalation from ticket creation: a fresh ticket is Open. */
export function ticketStatusLabel(t: {
  ticket_status?: unknown;
  outcome?: unknown;
} | null | undefined): "Open" | "In progress" | "Waiting" | "Resolved" {
  const status =
    t && typeof t.ticket_status === "string" ? t.ticket_status.toLowerCase() : "";
  const outcome =
    t && typeof t.outcome === "string" ? t.outcome.toLowerCase() : "";
  if (status === "resolved" || outcome === "resolved") return "Resolved";
  if (outcome === "waiting_on_employee") return "Waiting";
  if (status === "assigned" || outcome === "escalated") return "In progress";
  return "Open";
}

export interface KbClause extends Clause {
  overridden?: boolean;
}

export interface KbPayload {
  title: string;
  text: string;
  authority: string;
  keywords: string[];
  conflicts_with: string[];
}

export async function listThreads(): Promise<ThreadSummary[]> {
  return apiGet<ThreadSummary[]>("/threads");
}

export async function getThread(id: string): Promise<ThreadDetail> {
  return apiGet<ThreadDetail>(`/threads/${id}`);
}

export async function createThread(message: string): Promise<CreateThreadResponse> {
  // Identity comes from auth; the backend ignores any employee field.
  return requestJson<CreateThreadResponse>("POST", "/threads", { message });
}

/** POST a follow-up message; the AI agent run streams back as SSE events. */
export async function sendThreadMessage(
  id: string,
  message: string,
  onEvent: (ev: SseEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  return streamSSE(`/threads/${id}/messages`, { message }, onEvent, signal);
}

/** Subscribe to the live thread stream (admin join/reply/resolve notices). */
export async function subscribeThread(
  id: string,
  onEvent: (ev: SseEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  return streamGetSSE(`/threads/${id}/stream`, onEvent, signal);
}

export async function joinThread(id: string): Promise<ThreadSummary> {
  // Identity comes from auth; agent_name is accepted by the backend for
  // backward compatibility but ignored, so it is not sent.
  return requestJson<ThreadSummary>("POST", `/threads/${id}/join`, {});
}

export async function humanReply(
  id: string,
  message: string,
): Promise<{ message: BackendMessage; thread: ThreadSummary }> {
  return requestJson("POST", `/threads/${id}/human`, { message });
}

export async function resolveThread(id: string): Promise<ThreadSummary> {
  return requestJson<ThreadSummary>("POST", `/threads/${id}/resolve`, {});
}

export async function updateClause(
  id: string,
  payload: KbPayload,
): Promise<KbClause> {
  return apiPut<KbClause>(`/kb/${id}`, payload);
}

export async function createClause(
  payload: KbPayload & { id: string },
): Promise<KbClause> {
  return requestJson<KbClause>("POST", "/kb", payload);
}

/* ---- User management (admin): GET/POST /auth/users, PATCH /auth/users/{id} ---- */

export interface ManagedUser {
  id: string;
  email: string;
  name: string;
  role: string;
  is_active: boolean;
}

export async function listUsers(): Promise<ManagedUser[]> {
  return apiGet<ManagedUser[]>("/auth/users");
}

export async function createUser(payload: {
  name: string;
  email: string;
  role: string;
  password: string;
}): Promise<ManagedUser> {
  return requestJson<ManagedUser>("POST", "/auth/users", payload);
}

export async function updateUser(
  id: string,
  patch: { name?: string; role?: string; is_active?: boolean; password?: string },
): Promise<ManagedUser> {
  return requestJson<ManagedUser>(
    "PATCH",
    `/auth/users/${encodeURIComponent(id)}`,
    patch,
  );
}

/** Best-effort text extraction from an SSE event payload. */
export function eventText(data: unknown): string {
  if (typeof data === "string") return data;
  if (data && typeof data === "object") {
    const d = data as Record<string, unknown>;
    for (const k of ["text", "message", "detail", "reason"]) {
      if (typeof d[k] === "string" && (d[k] as string).length) {
        return d[k] as string;
      }
    }
    try {
      return JSON.stringify(data);
    } catch {
      return String(data);
    }
  }
  return String(data ?? "");
}
