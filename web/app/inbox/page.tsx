"use client";

/* /inbox — the single support operations queue. Live thread list (GET /inbox
 * preferred, GET /threads fallback), in-page filters Mine / Open /
 * Unassigned / Resolved / All + search, and a stats summary. Cards, not giant
 * tables; selection navigates to the workspace at /inbox/[id]. There is no
 * separate "My assigned" route: ?filter=mine is just this page filtered. */

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import {
  apiGet,
  listThreads,
  type Stats,
  type ThreadSummary,
} from "@/lib/api";
import { useAuth } from "@/components/auth-provider";
import { Chip, InlineError, friendlyError, outcomeLabel, outcomeTone, snippet } from "@/components/status";

type Filter = "open" | "mine" | "unassigned" | "resolved" | "all";

const FILTERS: Filter[] = ["open", "mine", "unassigned", "resolved", "all"];

function parseFilter(value: string | null): Filter | null {
  return value && (FILTERS as string[]).includes(value) ? (value as Filter) : null;
}

async function loadThreads(): Promise<ThreadSummary[]> {
  try {
    const inbox = await apiGet<ThreadSummary[] | { threads: ThreadSummary[] }>("/inbox");
    if (Array.isArray(inbox)) return inbox;
    if (inbox && Array.isArray((inbox as { threads: ThreadSummary[] }).threads)) {
      return (inbox as { threads: ThreadSummary[] }).threads;
    }
  } catch {
    /* fall through to /threads */
  }
  return listThreads();
}

function lastText(t: ThreadSummary): string {
  const lm = t.last_message as unknown;
  if (!lm) return t.text;
  if (typeof lm === "string") return lm;
  if (typeof lm === "object" && lm !== null && "text" in lm) {
    return String((lm as { text: unknown }).text ?? t.text);
  }
  return t.text;
}

/** True when the thread is assigned to the signed-in viewer. Prefers the
 * stable `assigned_to_user_id` when the backend sends it, else the display
 * name (legacy rows). */
function isMine(t: ThreadSummary, viewerId: string | null, meName: string): boolean {
  const threadUserId =
    typeof (t as { assigned_to_user_id?: unknown }).assigned_to_user_id === "string"
      ? ((t as { assigned_to_user_id?: string | null }).assigned_to_user_id ?? null)
      : null;
  if (threadUserId && viewerId) return threadUserId === viewerId;
  if (!meName) return false;
  return (t.assigned_to || "").toLowerCase() === meName;
}

export default function InboxPage() {
  return (
    <Suspense fallback={<main className="px-4 py-10 text-center text-sm text-slate-500">Loading inbox…</main>}>
      <InboxInner />
    </Suspense>
  );
}

function InboxInner() {
  const { user, loading: authLoading } = useAuth();
  const router = useRouter();
  const search = useSearchParams();
  // One queue, URL-shareable filter. The URL seeds state and stays in sync
  // both ways so /inbox?filter=mine deep-links to the Mine view.
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [filter, setFilter] = useState<Filter>(() => parseFilter(search.get("filter")) ?? "open");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const viewerId = user?.id ?? null;
  const meName = (user?.name || user?.email || "").toLowerCase();

  // URL -> state (back/forward, deep links, old bookmarks).
  useEffect(() => {
    const f = parseFilter(search.get("filter"));
    if (f) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setFilter((prev) => (prev === f ? prev : f));
    }
  }, [search]);

  const selectFilter = useCallback(
    (f: Filter) => {
      setFilter(f);
      const params = new URLSearchParams(search.toString());
      params.set("filter", f);
      router.replace(`/inbox?${params.toString()}`, { scroll: false });
    },
    [router, search],
  );

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [t, s] = await Promise.all([
        loadThreads(),
        apiGet<Stats>("/stats").catch(() => null),
      ]);
      setThreads(t);
      setStats(s);
      setError(null);
    } catch (e) {
      setError(friendlyError(e).message);
    } finally {
      setLoading(false);
    }
  }, []);

  // Initial load subscribes inline; updates land in the async callback.
  useEffect(() => {
    let cancelled = false;
    Promise.all([loadThreads(), apiGet<Stats>("/stats").catch(() => null)])
      .then(([t, s]) => {
        if (cancelled) return;
        setThreads(t);
        setStats(s);
        setError(null);
        setLoading(false);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setError(friendlyError(e).message);
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const counts = useMemo(() => {
    const open = threads.filter((t) => t.outcome !== "resolved");
    return {
      open: open.length,
      mine: threads.filter((t) => isMine(t, viewerId, meName) && t.outcome !== "resolved").length,
      unassigned: threads.filter((t) => !t.assigned_to && t.outcome !== "resolved").length,
      resolved: threads.filter((t) => t.outcome === "resolved").length,
      all: threads.length,
    };
  }, [meName, threads, viewerId]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return threads.filter((t) => {
      if (filter === "open" && t.outcome === "resolved") return false;
      if (filter === "resolved" && t.outcome !== "resolved") return false;
      if (filter === "mine" && (!isMine(t, viewerId, meName) || t.outcome === "resolved")) return false;
      if (filter === "unassigned" && (t.assigned_to || t.outcome === "resolved")) return false;
      if (q) {
        const hay = `${t.employee} ${t.text} ${lastText(t)} ${t.assigned_to ?? ""} ${t.request_id}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [filter, meName, query, threads, viewerId]);

  if (!authLoading && user?.role === "employee") {
    router.replace("/help");
    return (
      <main className="px-4 py-10 text-center text-sm text-slate-500">
        Employees use Help — redirecting…
      </main>
    );
  }

  const tabs: { key: Filter; label: string }[] = [
    { key: "open", label: `Open (${counts.open})` },
    { key: "mine", label: `Mine (${counts.mine})` },
    { key: "unassigned", label: `Unassigned (${counts.unassigned})` },
    { key: "resolved", label: `Resolved (${counts.resolved})` },
    { key: "all", label: `All (${counts.all})` },
  ];

  const queueStats = [
    { label: "Open", value: counts.open },
    { label: "Mine", value: counts.mine },
    { label: "Unassigned", value: counts.unassigned },
    { label: "Resolved", value: counts.resolved },
  ];

  return (
    <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-4 px-4 py-6">
      <div className="flex flex-wrap items-center gap-2">
        <div>
          <h1 className="text-xl font-bold tracking-tight">Support inbox</h1>
          <p className="text-sm text-slate-500">
            {stats
              ? `${stats.handled} handled · ${stats.resolved} resolved · ${stats.tickets} tickets`
              : "One operational queue — filter it, don't leave it"}
          </p>
        </div>
        <button
          onClick={() => void refresh()}
          className="ml-auto rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-100"
        >
          Refresh
        </button>
      </div>

      <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4" aria-label="Queue summary">
        {queueStats.map((s) => (
          <div
            key={s.label}
            className="rounded-lg border border-slate-200 bg-white px-3 py-2 shadow-sm"
          >
            <dt className="text-[11px] font-medium tracking-wide text-slate-500 uppercase">{s.label}</dt>
            <dd className="text-lg font-bold tabular-nums">{s.value}</dd>
          </div>
        ))}
      </dl>

      {error && <InlineError message={error} />}

      <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
        <div role="tablist" aria-label="Queue filters" className="flex flex-wrap gap-1.5">
          {tabs.map((t) => (
            <button
              key={t.key}
              role="tab"
              aria-selected={filter === t.key}
              onClick={() => selectFilter(t.key)}
              className={`rounded-full border px-3 py-1.5 text-xs font-medium ${
                filter === t.key
                  ? "border-indigo-600 bg-indigo-600 text-white"
                  : "border-slate-300 bg-white text-slate-600 hover:bg-slate-100"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search employee, text, or ticket…"
          aria-label="Search conversations"
          className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-900 placeholder:text-slate-400 focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100 sm:ml-auto sm:w-64"
        />
      </div>

      {loading ? (
        <p className="py-10 text-center text-sm text-slate-500">Loading conversations…</p>
      ) : visible.length === 0 ? (
        <div className="rounded-lg border border-dashed border-slate-300 bg-white px-4 py-12 text-center">
          <p className="text-sm font-medium">Nothing here</p>
          <p className="mt-1 text-xs text-slate-500">
            {query
              ? "No conversations match your search."
              : filter === "mine"
                ? "Nothing assigned to you — Join a conversation to take ownership."
                : "New employee requests will appear in this view."}
          </p>
        </div>
      ) : (
        <ol className="grid gap-2.5 md:grid-cols-2">
          {visible.map((t) => (
            <li key={t.request_id}>
              <Link
                href={`/inbox/${encodeURIComponent(t.request_id)}`}
                className="block rounded-lg border border-slate-200 bg-white p-3.5 shadow-sm transition-colors hover:border-indigo-300 hover:shadow"
              >
                <div className="flex items-center gap-2">
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-indigo-100 text-xs font-bold text-indigo-700">
                    {(t.employee || "?").slice(0, 1).toUpperCase()}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-semibold">{t.employee}</p>
                    <p className="truncate text-xs text-slate-500">{snippet(lastText(t), 90)}</p>
                  </div>
                  <Chip tone={outcomeTone(t.outcome)}>{outcomeLabel(t.outcome)}</Chip>
                </div>
                <div className="mt-2.5 flex flex-wrap items-center gap-1.5 text-xs">
                  {t.assigned_to ? (
                    <Chip tone="blue">→ {t.assigned_to}</Chip>
                  ) : (
                    <Chip>Unassigned</Chip>
                  )}
                  {t.ticket_status && <Chip tone="purple">Ticket: {t.ticket_status}</Chip>}
                  {t.ticket_id && <Chip>{t.ticket_id}</Chip>}
                  <span className="ml-auto text-slate-400">{t.message_count} msgs</span>
                </div>
              </Link>
            </li>
          ))}
        </ol>
      )}
    </main>
  );
}
