"use client";

/* /inbox/[id] — support conversation workspace. Transcript fed from GET
 * /threads/{id} + live stream; composer sends agent replies via POST human;
 * Join/Take ownership + Resolve use the signed-in identity (no name modal).
 * Side panel shows citation and audit context. 401/403/404/409 render
 * inline. */

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  useExternalStoreRuntime,
  type AppendMessage,
  type ThreadMessageLike,
} from "@assistant-ui/react";
import {
  backendToAui,
  ChatThread,
  extractText,
  type SupportViewer,
} from "@/components/aui-thread";
import {
  apiGet,
  getThread,
  humanReply,
  isAbortError,
  joinThread,
  resolveThread,
  subscribeThread,
  type BackendMessage,
  type KbClause,
  type ThreadDetail,
} from "@/lib/api";
import { useAuth } from "@/components/auth-provider";
import { Chip, InlineError, friendlyError, outcomeLabel, outcomeTone } from "@/components/status";

export default function InboxThreadPage() {
  const params = useParams();
  const raw = params.id;
  const id = Array.isArray(raw) ? raw[0] : (raw ?? "");
  const decoded = decodeURIComponent(id);
  const router = useRouter();
  const { user, loading: authLoading } = useAuth();

  const [detail, setDetail] = useState<ThreadDetail | null>(null);
  const [messages, setMessages] = useState<ThreadMessageLike[]>([]);
  const [isRunning, setIsRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [kb, setKb] = useState<KbClause[]>([]);
  const [busy, setBusy] = useState<"join" | "resolve" | null>(null);
  const localId = useRef(0);

  const agentName = (user?.name?.trim() || user?.email?.trim() || "Support").slice(0, 80);

  // Support perspective: employee incoming (left), viewer's own human
  // messages outgoing (right). Identity prefers the stable
  // `assigned_to_user_id`/`user.id` and falls back to display names.
  const viewer: SupportViewer = useMemo(
    () => ({ id: user?.id ?? null, name: user?.name ?? null, email: user?.email ?? null }),
    [user?.email, user?.id, user?.name],
  );
  const assignedToUserId =
    typeof (detail as { assigned_to_user_id?: unknown } | null)?.assigned_to_user_id ===
    "string"
      ? ((detail as { assigned_to_user_id?: string | null }).assigned_to_user_id ?? null)
      : null;

  // Latest assignment for the live-stream callback without re-subscribing on
  // every detail refresh. Name matching handles the common case; the ref
  // covers nameless legacy rows owned by the viewer.
  const assignRef = useRef<{ assignedTo: string | null; assignedToUserId: string | null }>({
    assignedTo: null,
    assignedToUserId: null,
  });
  useEffect(() => {
    assignRef.current = {
      assignedTo: detail?.assigned_to ?? null,
      assignedToUserId,
    };
  }, [detail?.assigned_to, assignedToUserId]);

  const refresh = useCallback(async () => {
    if (!decoded) return;
    try {
      const d = await getThread(decoded);
      setDetail(d);
      setMessages(
        backendToAui(d.messages, {
          perspective: "support",
          viewer,
          assignedTo: d.assigned_to ?? null,
          assignedToUserId:
            typeof (d as { assigned_to_user_id?: unknown }).assigned_to_user_id ===
            "string"
              ? ((d as { assigned_to_user_id?: string | null }).assigned_to_user_id ?? null)
              : null,
        }),
      );
      setError(null);
    } catch (e) {
      setError(friendlyError(e).message);
    }
  }, [decoded, viewer]);

  // Initial thread + reference data load inline; updates land in callbacks.
  // Re-runs when the viewer identity resolves so own/outgoing mapping is
  // correct even if auth loaded after the thread.
  useEffect(() => {
    if (!decoded) return;
    let cancelled = false;
    getThread(decoded)
      .then((d) => {
        if (cancelled) return;
        setDetail(d);
        setMessages(
          backendToAui(d.messages, {
            perspective: "support",
            viewer,
            assignedTo: d.assigned_to ?? null,
            assignedToUserId:
              typeof (d as { assigned_to_user_id?: unknown }).assigned_to_user_id ===
              "string"
                ? ((d as { assigned_to_user_id?: string | null }).assigned_to_user_id ?? null)
                : null,
          }),
        );
        setError(null);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setError(friendlyError(e).message);
      });
    apiGet<KbClause[]>("/kb")
      .then((k) => {
        if (!cancelled) setKb(k);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [decoded, viewer]);

  useEffect(() => {
    if (!decoded) return;
    const ctrl = new AbortController();
    // Strict-mode mounts, unmounts, then re-subscribes: the first cleanup
    // must be silent and safe to run twice. `completed` tracks a normally
    // finished stream so cleanup never aborts an already-completed signal
    // (whose abort reason would otherwise surface as a console AbortError).
    let settled = false;
    let aborted = false;
    let completed = false;
    const viewerSnapshot = viewer;
    void subscribeThread(
      decoded,
      (ev) => {
        if (settled || completed) return;
        if (ctrl.signal.aborted) return;
        if (ev.kind !== "message") return;
        const m = ev.data as BackendMessage;
        if (typeof m?.seq !== "number" || typeof m?.text !== "string") return;
        const mapped = backendToAui([m], {
          perspective: "support",
          viewer: viewerSnapshot,
          assignedTo: assignRef.current.assignedTo,
          assignedToUserId: assignRef.current.assignedToUserId,
        })[0];
        // Stable `m-{seq}` id keeps dedup exact; the follow-up refresh
        // re-resolves against the canonical thread assignment.
        setMessages((prev) => (prev.some((x) => x.id === mapped.id) ? prev : [...prev, mapped]));
        void refresh();
      },
      ctrl.signal,
    )
      .then(() => {
        completed = true;
      })
      .catch((e: unknown) => {
        // Expected teardown (route change, ticket switch, strict-mode
        // remount) resolves silently inside the stream helper; anything
        // arriving here with an aborted signal is still an expected abort
        // and must never reach the UI as an error. Real network/API
        // errors fall through to the inline error state.
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
  }, [decoded, refresh, viewer]);

  const doReply = useCallback(
    async (text: string) => {
      if (!user) {
        setError("Please sign in to reply.");
        return;
      }
      setIsRunning(true);
      setError(null);
      localId.current += 1;
      // Own/outgoing bubble: never prefix the display name into the text.
      // The canonical backend message renders the same way (role=user right).
      const optimistic: ThreadMessageLike = {
        id: `local-agent-${localId.current}`,
        role: "user",
        content: [{ type: "text", text }],
      };
      setMessages((prev) => [...prev, optimistic]);
      try {
        await humanReply(decoded, text);
        await refresh();
      } catch (e) {
        setMessages((prev) => prev.filter((m) => m.id !== optimistic.id));
        const f = friendlyError(e);
        setError(
          f.status === 409
            ? "Take ownership (Join) before replying — the thread has no owner yet."
            : f.message,
        );
      } finally {
        setIsRunning(false);
      }
    },
    [decoded, refresh, user],
  );

  const onNew = useCallback(
    async (message: AppendMessage) => {
      const text = extractText(message);
      if (!text || isRunning) return;
      await doReply(text);
    },
    [doReply, isRunning],
  );

  // Composer gate: the human reply slot stays enabled when assigned and
  // not resolved; it locks while the reply request is submitting and once
  // the thread is resolved. `isRunning` here is human-reply submitting
  // state (never an AI generation), so the Stop control stays off via the
  // explicit `showStop={false}` on ChatThread below.
  const resolved = detail?.outcome === "resolved";
  const composerLocked = isRunning || resolved;

  const runtime = useExternalStoreRuntime({
    isRunning,
    messages,
    isDisabled: composerLocked,
    isSendDisabled: composerLocked,
    convertMessage: (m) => m,
    onNew,
  });

  const takeOwnership = useCallback(async () => {
    if (!user) {
      setError("Please sign in to take ownership.");
      return;
    }
    setBusy("join");
    setError(null);
    try {
      // Identity comes from auth; no name is sent.
      const updated = await joinThread(decoded);
      setDetail((prev) => (prev ? { ...prev, ...updated } : prev));
      setNotice(`You're now the owner — ${updated.assigned_to} is shown to the employee.`);
      await refresh();
    } catch (e) {
      setError(friendlyError(e).message);
    } finally {
      setBusy(null);
    }
  }, [decoded, refresh, user]);

  const resolve = useCallback(async () => {
    if (!user) {
      setError("Please sign in to resolve.");
      return;
    }
    setBusy("resolve");
    setError(null);
    try {
      const updated = await resolveThread(decoded);
      setDetail((prev) => (prev ? { ...prev, ...updated } : prev));
      setNotice(`Resolved by ${agentName}. The employee sees a resolution notice.`);
      await refresh();
    } catch (e) {
      const f = friendlyError(e);
      setError(
        f.status === 409 ? "Take ownership (Join) before resolving." : f.message,
      );
    } finally {
      setBusy(null);
    }
  }, [agentName, decoded, refresh, user]);

  if (!authLoading && user?.role === "employee") {
    router.replace("/help");
    return null;
  }

  const kbById = new Map(kb.map((c) => [c.id, c]));
  const cited = (detail?.cited ?? []).map((cid) => kbById.get(cid)).filter((c) => c !== undefined);
  // Ownership prefers the stable id; display-name fallback is
  // case-insensitive for legacy rows that predate `assigned_to_user_id`.
  const ownedByMe = assignedToUserId
    ? !!user?.id && user.id === assignedToUserId
    : !!detail?.assigned_to &&
      !!agentName &&
      detail.assigned_to.trim().toLowerCase() === agentName.trim().toLowerCase();
  const unowned = !detail?.assigned_to;

  return (
    <main className="chat-workspace chat-workspace-lg mx-auto flex w-full max-w-6xl min-h-0 min-w-0 flex-1 flex-col gap-4 px-4 py-6">
      <div className="flex flex-wrap items-center gap-2">
        <div className="min-w-0">
          <Link href="/inbox" className="text-xs font-medium text-indigo-600 hover:underline">
            ← Inbox
          </Link>
          <h1 className="truncate text-lg font-bold tracking-tight">
            {detail ? detail.employee : "Conversation"}
          </h1>
          <p className="truncate text-xs text-slate-500">
            {detail ? detail.text : decoded}
          </p>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          {detail && (
            <>
              <Chip tone={outcomeTone(detail.outcome)}>{outcomeLabel(detail.outcome)}</Chip>
              {detail.assigned_to ? <Chip tone="blue">→ {detail.assigned_to}</Chip> : <Chip>Unassigned</Chip>}
              {detail.ticket_status && <Chip tone="purple">Ticket: {detail.ticket_status}</Chip>}
            </>
          )}
        </div>
      </div>

      {error && <InlineError message={error} />}
      {notice && (
        <p className="rounded-lg border border-green-200 bg-green-50 px-3 py-2 text-sm text-green-800">
          {notice}
        </p>
      )}

      <div className="flex flex-wrap gap-2">
        <button
          onClick={() => void takeOwnership()}
          disabled={busy !== null || ownedByMe || resolved}
          className="rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
        >
          {busy === "join" ? "Joining…" : ownedByMe ? "You own this" : unowned ? "Join — take ownership" : `Take over from ${detail?.assigned_to}`}
        </button>
        <button
          onClick={() => void resolve()}
          disabled={busy !== null || resolved}
          className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-100 disabled:opacity-50"
        >
          {busy === "resolve" ? "Resolving…" : resolved ? "Resolved" : "Resolve"}
        </button>
        <span className="self-center text-xs text-slate-500">
          Replying as {agentName}
        </span>
      </div>

      <div className="grid min-h-0 min-w-0 flex-1 gap-4 lg:grid-cols-[1fr_300px]">
        <section
          aria-label="Transcript"
          className="chat-box flex min-h-0 min-w-0 flex-col overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm"
        >
          <div className="flex min-h-0 min-w-0 flex-1 flex-col">
            <ChatThread
              runtime={runtime}
              welcome="Transcript loads here. Reply below as support — the employee sees your messages live."
              placeholder={resolved ? "Resolved — no further replies needed" : `Reply as ${agentName}…`}
              composerDisabled={composerLocked}
              showStop={false}
            />
          </div>
        </section>

        <aside aria-label="Context" className="flex min-h-0 flex-col gap-3 lg:overflow-y-auto">
          <div className="rounded-lg border border-slate-200 bg-white p-3 shadow-sm">
            <h2 className="text-sm font-semibold text-slate-900">Cited policy ({cited.length})</h2>
            {cited.length === 0 ? (
              <p className="mt-1 text-xs text-slate-500">The agent hasn&apos;t cited policy yet.</p>
            ) : (
              cited.map((c) => (
                <article key={c!.id} className="mt-2 rounded-lg border border-slate-200 bg-slate-50 p-2 text-xs">
                  <p className="font-mono font-bold text-slate-900">{c!.id} <span className="font-sans font-normal text-slate-500">· {c!.authority}</span></p>
                  <p className="mt-0.5 font-medium text-slate-900">{c!.title}</p>
                  <p className="mt-0.5 text-slate-600">{c!.text}</p>
                </article>
              ))
            )}
          </div>

          <details className="rounded-lg border border-slate-200 bg-white p-3 shadow-sm">
            <summary className="cursor-pointer text-sm font-semibold">
              Audit trail ({detail?.actions.length ?? 0} actions)
            </summary>
            <div className="mt-2 space-y-2">
              {(detail?.turns ?? []).map((t, i) => (
                <p key={i} className="text-xs"><strong>{t.speaker}:</strong> {t.text}</p>
              ))}
              {(detail?.actions ?? []).map((a, i) => (
                <p key={i} className="rounded border border-slate-200 bg-slate-50 p-1.5 font-mono text-[11px] text-slate-700">
                  {i + 1}. {a.tool}
                  {"refused" in a && typeof a.refused === "string" ? ` — refused: ${String(a.refused)}` : ""}
                </p>
              ))}
              {(detail?.actions.length ?? 0) === 0 && (detail?.turns.length ?? 0) === 0 && (
                <p className="text-xs text-slate-500">No agent actions recorded yet.</p>
              )}
            </div>
          </details>
        </aside>
      </div>
    </main>
  );
}
