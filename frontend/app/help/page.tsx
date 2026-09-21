"use client";

/* /help — the single employee ticket workspace.
 *
 * One ticket workspace, three panes: left rail (Help heading, New ticket,
 * Your tickets), center (selected ticket transcript + composer), right
 * (Ticket details). New ticket is a local draft — no backend thread exists
 * until the employee sends the first message, at which point we call the
 * existing createThread(message) and then start the AI SSE run. The ticket
 * number comes from the run (raise_ticket tool result) and from GET
 * /threads/{id} actions afterwards; escalation is never inferred from ticket
 * creation. Identity comes from auth. Live human join/reply/resolve notices
 * arrive via the thread subscription. Message identity is the stable backend
 * `m-{seq}` id; dedup is by id only, never by text. */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  useExternalStoreRuntime,
  type AppendMessage,
  type ThreadMessageLike,
} from "@assistant-ui/react";
import { backendToAui, ChatThread, extractText } from "@/components/aui-thread";
import { TicketDetails } from "@/components/employee/ticket-details";
import { appendById, canonicalId, seqFromSayData } from "@/lib/reconcile";
import {
  createThread,
  eventText,
  getThread,
  isAbortError,
  listThreads,
  resolveEmployeeTicketId,
  sendThreadMessage,
  subscribeThread,
  threadTicketId,
  ticketStatusLabel,
  type BackendMessage,
  type ThreadDetail,
  type ThreadSummary,
} from "@/lib/api";
import { useAuth } from "@/components/auth-provider";
import { Chip, InlineError, friendlyError, snippet } from "@/components/status";

const THREAD_KEY = "veridian-thread";
const QUICK_ANSWERS = [
  "How do I reset my password?",
  "My printer says paper jam even when there isn't one.",
];
const NEEDS_REVIEW = [
  "I need access to the expense management tool.",
  "I need approval to install software that's not in the catalog.",
];

function partText(m: ThreadMessageLike): string {
  const c = m.content;
  if (typeof c === "string") return c;
  const first = c[0];
  return first && first.type === "text" ? first.text : "";
}

/* Message identity is the stable backend `m-{seq}` id (see
 * `backendToAui`). Dedup is by id only, never by text: repeated text can be
 * a legitimate repeat and must stay visible. Temporary `local-*` bubbles are
 * reconciled to `m-{seq}` via the `seq` on `say` events (see
 * `@/lib/reconcile`). */

/* Pending-handoff gate for the employee composer: the thread was handed
 * off/escalated (`outcome` case-insensitive `escalated`) and no IT agent
 * has joined yet (no `assigned_to`/`assigned_to_user_id`). Drafts (no
 * selected thread) are never locked; `waiting_on_employee` is a real AI
 * follow-up the employee must answer, so it stays enabled; an assigned
 * thread (human joined) re-enables; `resolved` threads are left to backend
 * reopen rules (never locked here). */
function isPendingHandoff(
  selectedId: string | null,
  detail: ThreadDetail | null,
): boolean {
  if (!selectedId || !detail) return false;
  const outcome =
    typeof detail.outcome === "string" ? detail.outcome.toLowerCase() : "";
  if (outcome !== "escalated") return false;
  const owner =
    typeof detail.assigned_to === "string"
      ? detail.assigned_to.trim()
      : detail.assigned_to;
  if (owner) return false;
  const ownerId = (
    detail as { assigned_to_user_id?: unknown }
  ).assigned_to_user_id;
  if (typeof ownerId === "string" ? ownerId.trim() : ownerId) return false;
  return true;
}

export default function HelpPage() {
  const { user, loading: authLoading } = useAuth();
  // null = local draft (New ticket): no backend thread until first send.
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ThreadDetail | null>(null);
  const [messages, setMessages] = useState<ThreadMessageLike[]>([]);
  const [isRunning, setIsRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mine, setMine] = useState<ThreadSummary[]>([]);
  // Ticket numbers known per thread (from detail actions + live SSE runs).
  // List rows only carry summaries, so this map fills in ticket badges.
  const [ticketMap, setTicketMap] = useState<Record<string, string>>({});
  // Ticket raised during the active run, before the detail refresh lands.
  const [liveTicketId, setLiveTicketId] = useState<string | null>(null);
  const selectedRef = useRef<string | null>(null);
  const runRef = useRef<AbortController | null>(null);
  const localId = useRef(0);

  const identity = useMemo(
    () => user?.name?.trim() || user?.email?.trim() || "You",
    [user],
  );

  const refreshDetail = useCallback(async (id: string) => {
    try {
      const d = await getThread(id);
      // Canonical source of truth: backend seqs are unique, so repeated text
      // stays as repeated bubbles. No text-based collapsing here.
      if (selectedRef.current !== id) return null;
      setDetail(d);
      setMessages(backendToAui(d.messages));
      const ticket = threadTicketId(d);
      if (ticket) {
        setTicketMap((prev) => (prev[id] === ticket ? prev : { ...prev, [id]: ticket }));
      }
      return d;
    } catch {
      return null;
    }
  }, []);

  const refreshMine = useCallback(async () => {
    try {
      const all = await listThreads();
      // Stable order, newest first; dedup strictly by request_id.
      const seen = new Set<string>();
      const ordered = all
        .slice()
        .reverse()
        .filter((t) => {
          if (seen.has(t.request_id)) return false;
          seen.add(t.request_id);
          return true;
        });
      setMine(ordered);
    } catch {
      /* ticket list is best-effort */
    }
  }, []);

  // Restore the previously selected ticket.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      let prev: string | null = null;
      try {
        prev = localStorage.getItem(THREAD_KEY);
      } catch {
        prev = null;
      }
      if (!prev) return;
      selectedRef.current = prev;
      setSelectedId(prev);
      try {
        const d = await getThread(prev);
        if (cancelled) return;
        setDetail(d);
        setMessages(backendToAui(d.messages));
        const ticket = threadTicketId(d);
        if (ticket) setTicketMap((m) => ({ ...m, [prev as string]: ticket }));
      } catch {
        if (cancelled) return;
        selectedRef.current = null;
        setSelectedId(null);
        try {
          localStorage.removeItem(THREAD_KEY);
        } catch {
          /* ignore */
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Ticket list loads inline (state updates happen in the async callback).
  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    listThreads()
      .then((all) => {
        if (cancelled) return;
        const seen = new Set<string>();
        setMine(
          all
            .slice()
            .reverse()
            .filter((t) => {
              if (seen.has(t.request_id)) return false;
              seen.add(t.request_id);
              return true;
            }),
        );
      })
      .catch(() => {
        /* ticket list is best-effort */
      });
    return () => {
      cancelled = true;
    };
  }, [user]);

  // Live stream: human join/reply/resolve notices for the selected ticket.
  // Same teardown contract as the IT inbox subscription: expected aborts
  // (ticket switch, unmount, strict-mode remount) stay silent, real errors
  // surface, stale callbacks never touch state, and cleanup never aborts
  // an already-completed signal.
  useEffect(() => {
    if (!selectedId) return;
    const id = selectedId;
    const ctrl = new AbortController();
    let settled = false;
    let aborted = false;
    let completed = false;
    void subscribeThread(
      id,
      (ev) => {
        if (settled || completed) return;
        if (ctrl.signal.aborted) return;
        if (selectedRef.current !== id) return;
        if (ev.kind !== "message") return;
        const m = ev.data as BackendMessage;
        if (typeof m?.seq !== "number" || typeof m?.text !== "string") return;
        const mapped = backendToAui([m])[0];
        // Reconcile by stable id only. The active `say` stream uses the
        // canonical `m-{seq}` id up front (see `pushAssistant`), so a replay
        // of the same backend message is a no-op here. Never compare text:
        // identical words can be legitimate repeats. Backend join, escalation,
        // and human messages are preserved as-is; the pending-handoff state
        // lives only in the Ticket details card.
        setMessages((prev) => appendById(prev, mapped));
        // Assignment / status may have changed — refresh the side panel.
        void refreshDetail(id);
        void refreshMine();
      },
      ctrl.signal,
    )
      .then(() => {
        completed = true;
      })
      .catch((e: unknown) => {
        if (settled) return;
        if (isAbortError(e)) return;
        if (ctrl.signal.aborted) return;
        setError(friendlyError(e).message);
      });
    return () => {
      if (aborted) return;
      aborted = true;
      settled = true;
      if (completed || ctrl.signal.aborted) return;
      try {
        ctrl.abort();
      } catch {
        /* abort is idempotent; expected AbortErrors stay silent */
      }
    };
  }, [selectedId, refreshDetail, refreshMine]);

  const runAgent = useCallback(
    async (id: string, text: string, ctrl: AbortController) => {
      await sendThreadMessage(
        id,
        text,
        (ev) => {
          const d = ev.data as Record<string, unknown>;
          switch (ev.kind) {
            case "say": {
              const seq = seqFromSayData(ev.data);
              const chunk = eventText(ev.data);
              if (!chunk) break;
              if (seq !== null) {
                // Streamed `say` events carry the canonical backend `seq`.
                // Using `m-{seq}` up front means the live subscription replay
                // of the same backend message dedups by id. No text
                // comparison: a legitimate repeat stays as two bubbles.
                const canonical = canonicalId(seq);
                setMessages((prev) => {
                  const existing = prev.find((m) => m.id === canonical);
                  if (!existing) {
                    return [
                      ...prev,
                      { id: canonical, role: "assistant", content: [{ type: "text", text: chunk }] },
                    ];
                  }
                  const cur = partText(existing);
                  if (!cur) {
                    return prev.map((m) =>
                      m.id === canonical
                        ? { ...m, content: [{ type: "text", text: chunk }] }
                        : m,
                    );
                  }
                  // Same-id replay: idempotent no-op. Scoped to one stable
                  // id, never across messages.
                  if (cur.includes(chunk)) return prev;
                  return prev.map((m) =>
                    m.id === canonical
                      ? { ...m, content: [{ type: "text", text: `${cur}${chunk}` }] }
                      : m,
                  );
                });
              } else {
                localId.current += 1;
                const lid = `local-a-${localId.current}`;
                setMessages((prev) => [
                  ...prev,
                  { id: lid, role: "assistant", content: [{ type: "text", text: chunk }] },
                ]);
              }
              break;
            }
            case "refused":
              localId.current += 1;
              setMessages((prev) => [
                ...prev,
                {
                  id: `local-a-${localId.current}`,
                  role: "assistant",
                  content: [{ type: "text", text: `I can't help with that: ${eventText(ev.data)}` }],
                },
              ]);
              break;
            case "tool_result": {
              // A raised ticket surfaces here ({ticket, category, priority}).
              // Show it immediately in the list + details panel. This says
              // nothing about escalation — ticket creation is not escalation.
              const r = d.result as Record<string, unknown> | undefined;
              const ticket =
                r && typeof r.ticket === "string" && r.ticket ? r.ticket : null;
              if (ticket) {
                setLiveTicketId(ticket);
                setTicketMap((prev) =>
                  prev[id] === ticket ? prev : { ...prev, [id]: ticket },
                );
              }
              break;
            }
            case "outcome":
            case "done":
              // Outcome drives the status chip via the detail refresh below.
              // Never inferred from ticket creation.
              break;
            case "error":
              setError(eventText(ev.data));
              break;
            default:
              break;
          }
        },
        ctrl.signal,
      );
    },
    [],
  );

  // Composer gate: lock while a run is active or while a handed-off
  // thread waits for a human to join. Drafts (no selection) and AI
  // follow-ups (including `waiting_on_employee`, whose outcome is not
  // `escalated`) stay sendable; an assigned thread re-enables on join.
  const pendingHandoff = isPendingHandoff(selectedId, detail);
  const composerLocked = isRunning || pendingHandoff;
  // Employee Stop action: abort the in-flight AI SSE run. Wired as the
  // runtime `onCancel` so the Stop (Cancel) slot control is enabled while
  // running; the `onNew` finally-block settles `isRunning` afterwards.
  const cancelRun = useCallback(async () => {
    runRef.current?.abort();
  }, []);

  const onNew = useCallback(
    async (message: AppendMessage) => {
      const text = extractText(message);
      if (!text || isRunning) return;
      // Handed off with no IT owner yet: wait for a human to join. Drafts
      // and `waiting_on_employee` follow-ups never reach this gate locked.
      if (pendingHandoff) return;
      if (!user) {
        setError("Please sign in to start a ticket.");
        return;
      }
      setError(null);
      runRef.current?.abort();
      const ctrl = new AbortController();
      runRef.current = ctrl;
      localId.current += 1;
      setMessages((prev) => [
        ...prev,
        { id: `local-u-${localId.current}`, role: "user", content: [{ type: "text", text }] },
      ]);
      setIsRunning(true);
      try {
        let id = selectedRef.current;
        if (!id) {
          // Draft becomes real: create the backend thread only on first send.
          const created = await createThread(text);
          id = created.request_id;
          selectedRef.current = id;
          setSelectedId(id);
          try {
            localStorage.setItem(THREAD_KEY, id);
          } catch {
            /* ignore */
          }
          // The backend already stored this message at creation time, so
          // replace the optimistic bubble instead of appending.
          setMessages(backendToAui(created.messages));
          const createdTicket =
            typeof created.ticket_id === "string" && created.ticket_id
              ? created.ticket_id
              : typeof created.ticket === "string" && created.ticket
                ? created.ticket
                : null;
          if (createdTicket) {
            setLiveTicketId(createdTicket);
            setTicketMap((prev) => ({ ...prev, [id as string]: createdTicket }));
          }
          // Show the ticket in the left list immediately.
          const summary: ThreadSummary = {
            request_id: id,
            employee: identity,
            text,
            outcome: null,
            assigned_to: null,
            ticket_status: created.ticket_status ?? null,
            message_count: created.messages.length,
            last_message: null,
            cited: [],
            ticket_id: createdTicket,
            ticket: createdTicket,
          };
          setMine((prev) =>
            prev.some((t) => t.request_id === id) ? prev : [summary, ...prev],
          );
        }
        await runAgent(id, text, ctrl);
        await refreshDetail(id);
        await refreshMine();
      } catch (e) {
        if (!isAbortError(e)) setError(friendlyError(e).message);
      } finally {
        setIsRunning(false);
      }
    },
    [identity, isRunning, pendingHandoff, refreshDetail, refreshMine, runAgent, user],
  );

  const runtime = useExternalStoreRuntime({
    isRunning,
    messages,
    isDisabled: composerLocked,
    isSendDisabled: composerLocked,
    convertMessage: (m) => m,
    onNew,
    onCancel: cancelRun,
  });

  const newTicket = useCallback(() => {
    // Back to a local draft: clear the selection, show an empty composer.
    // No backend thread is created until the first message is sent.
    runRef.current?.abort();
    selectedRef.current = null;
    setSelectedId(null);
    setDetail(null);
    setMessages([]);
    setError(null);
    setLiveTicketId(null);
    try {
      localStorage.removeItem(THREAD_KEY);
    } catch {
      /* ignore */
    }
  }, []);

  const openTicket = useCallback(
    async (id: string) => {
      if (id === selectedRef.current) return;
      runRef.current?.abort();
      selectedRef.current = id;
      setSelectedId(id);
      setLiveTicketId(null);
      try {
        localStorage.setItem(THREAD_KEY, id);
      } catch {
        /* ignore */
      }
      setError(null);
      await refreshDetail(id);
    },
    [refreshDetail],
  );

  const selectedSummary = mine.find((t) => t.request_id === selectedId) ?? null;
  // Fallback order: detail ticket, selected list summary ticket, known live
  // ticket map, in-flight live ticket, then unavailable. The summary step
  // covers the detail-refresh race where the list already carries ticket_id.
  const ticketId = resolveEmployeeTicketId({
    detail,
    summary: selectedSummary,
    ticketMap,
    selectedId,
    liveTicketId,
  });

  return (
    <main className="chat-workspace chat-workspace-md mx-auto flex w-full max-w-7xl min-w-0 flex-1 flex-col gap-4 overflow-x-hidden px-4 py-6 min-h-0">
      {error && <InlineError message={error} />}

      <div className="grid min-h-0 min-w-0 flex-1 gap-4 md:grid-cols-[248px_minmax(0,1fr)] md:grid-rows-[minmax(0,1fr)_auto] xl:grid-cols-[280px_minmax(0,1fr)_300px]">
        {/* Left rail */}
        <aside
          aria-label="Your tickets"
          className="flex min-h-0 min-w-0 flex-col overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm"
        >
          <div className="border-b border-slate-200 bg-slate-50/60 p-4">
            <h1 className="text-lg font-bold tracking-tight">Help</h1>
            <p className="mt-0.5 text-xs text-slate-500">
              {user ? `Chatting as ${identity}` : "IT support"} · replies are live
            </p>
            <button
              onClick={newTicket}
              className="mt-3 w-full rounded-md bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-500"
            >
              New ticket
            </button>
          </div>
          <h2 className="px-4 pt-3 text-xs font-semibold tracking-wide text-slate-500 uppercase">
            Your tickets {mine.length ? `(${mine.length})` : ""}
          </h2>
          <ol className="max-h-56 min-h-0 min-w-0 space-y-1 overflow-y-auto p-2 md:max-h-none md:flex-1">
            {mine.length === 0 && (
              <li className="px-2 py-4 text-center text-xs text-slate-500">
                No tickets yet — describe your issue to start one.
              </li>
            )}
            {mine.map((t) => {
              const active = t.request_id === selectedId;
              const status = ticketStatusLabel(t);
              const ticket = ticketMap[t.request_id] ?? t.ticket_id ?? null;
              return (
                <li key={t.request_id} className="min-w-0">
                  <button
                    onClick={() => void openTicket(t.request_id)}
                    aria-current={active ? "true" : undefined}
                    className={`block w-full min-w-0 rounded-lg border px-2.5 py-2 text-left hover:bg-slate-50 ${
                      active
                        ? "border-indigo-300 bg-indigo-50/60"
                        : "border-transparent"
                    }`}
                  >
                    <span className="block truncate text-sm font-medium">
                      {snippet(t.text, 60)}
                    </span>
                    <span className="mt-1 flex flex-wrap items-center gap-1.5">
                      <Chip tone={status === "Resolved" ? "green" : "zinc"}>{status}</Chip>
                      {ticket && (
                        <span className="font-mono text-[11px] text-slate-500">{ticket}</span>
                      )}
                    </span>
                  </button>
                </li>
              );
            })}
          </ol>
        </aside>

        {/* Center pane */}
        <section
          aria-label="Ticket conversation"
          className="chat-box flex min-h-0 min-w-0 flex-col overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm"
        >
          <div className="min-w-0 border-b border-slate-200 bg-slate-50/60 px-4 py-2.5">
            <p className="truncate text-sm font-semibold">
              {selectedId
                ? (selectedSummary?.text ? snippet(selectedSummary.text, 80) : detail?.text ? snippet(detail.text, 80) : selectedId)
                : "New ticket"}
            </p>
            <p className="truncate text-xs text-slate-500">
              {selectedId
                ? ticketId
                  ? `Ticket ${ticketId}`
                  : "Ticket details unavailable"
                : "Describe your issue — nothing is created until you send."}
            </p>
          </div>
          {!authLoading && !user && (
            <p className="border-b border-slate-200 bg-amber-50 px-4 py-2 text-sm text-amber-800">
              You&apos;re not signed in — <a className="underline" href="/login">sign in</a> to keep your tickets.
            </p>
          )}
          {messages.length === 0 && !isRunning && (
            <div className="border-b border-slate-200 bg-white px-4 py-4">
              <p className="text-sm font-medium text-slate-900">Start with a common issue</p>
              <p className="mt-1 text-xs text-slate-500">Quick answers resolve instantly · Needs review routes to IT</p>
              <div className="mt-3 space-y-3">
                <div>
                  <p className="text-[11px] font-semibold tracking-wide text-slate-500 uppercase">Quick answers</p>
                  <div className="mt-1.5 flex flex-wrap gap-2">
                    {QUICK_ANSWERS.map((s) => (
                      <button
                        key={s}
                        onClick={() => void onNew({ content: [{ type: "text", text: s }] } as unknown as AppendMessage)}
                        disabled={composerLocked}
                        className="rounded-full border border-slate-300 bg-white px-3 py-1.5 text-xs text-slate-700 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {s}
                      </button>
                    ))}
                  </div>
                </div>
                <div>
                  <p className="text-[11px] font-semibold tracking-wide text-slate-500 uppercase">Needs review</p>
                  <div className="mt-1.5 flex flex-wrap gap-2">
                    {NEEDS_REVIEW.map((s) => (
                      <button
                        key={s}
                        onClick={() => void onNew({ content: [{ type: "text", text: s }] } as unknown as AppendMessage)}
                        disabled={composerLocked}
                        className="rounded-full border border-dashed border-slate-300 bg-slate-50 px-3 py-1.5 text-xs text-slate-700 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {s}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          )}
          <div className="flex min-h-0 min-w-0 flex-1 flex-col">
            <ChatThread
              runtime={runtime}
              welcome="Describe your IT issue and the support agent will reply here. If a person joins, you'll see their messages inline — nothing to install, nothing to refresh."
              placeholder={
                pendingHandoff
                  ? "Waiting for IT support to join…"
                  : isRunning
                    ? "Waiting for a reply…"
                    : "Describe your issue…"
              }
              composerDisabled={composerLocked}
            />
          </div>
        </section>

        {/* Right pane */}
        <aside
          aria-label="Ticket details"
          className="min-h-0 min-w-0 md:col-span-2 xl:col-span-1 xl:overflow-y-auto"
        >
          <TicketDetails detail={detail} ticketId={ticketId} liveTicketId={liveTicketId} />
        </aside>
      </div>
    </main>
  );
}
