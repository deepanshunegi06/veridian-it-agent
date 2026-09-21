"use client";

/* /manage/kb — admin knowledge-base editor. Searchable clause cards with
 * override/conflict badges, inline edit (PUT) and add (POST). 404/409/422
 * render inline. */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  apiGet,
  createClause,
  updateClause,
  type KbClause,
  type KbPayload,
} from "@/lib/api";
import { useAuth } from "@/components/auth-provider";
import { Chip, InlineError, friendlyError } from "@/components/status";

function csvToList(s: string): string[] {
  return s.split(",").map((x) => x.trim()).filter((x) => x.length > 0);
}

const emptyForm = { id: "", title: "", text: "", authority: "", keywords: "", conflicts: "" };

function ClauseForm({
  initial,
  withId,
  busy,
  onSubmit,
}: {
  initial: typeof emptyForm;
  withId: boolean;
  busy: boolean;
  onSubmit: (form: typeof emptyForm) => void;
}) {
  const [form, setForm] = useState(initial);
  const set = (k: keyof typeof emptyForm) => (v: string) =>
    setForm((prev) => ({ ...prev, [k]: v }));
  const valid =
    form.title.trim().length > 0 &&
    form.text.trim().length > 0 &&
    (!withId || form.id.trim().length > 0);

  const inputCls =
    "w-full rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-900 placeholder:text-slate-400 focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100";

  return (
    <div className="flex flex-col gap-2">
      {withId && (
        <input value={form.id} onChange={(e) => set("id")(e.target.value)} placeholder="Clause id (e.g. KB-12)" aria-label="Clause id" className={inputCls} />
      )}
      <input value={form.title} onChange={(e) => set("title")(e.target.value)} placeholder="Title" aria-label="Title" className={inputCls} />
      <textarea value={form.text} onChange={(e) => set("text")(e.target.value)} placeholder="Clause text — what support should cite" aria-label="Clause text" rows={3} className={inputCls} />
      <div className="grid gap-2 sm:grid-cols-3">
        <input value={form.authority} onChange={(e) => set("authority")(e.target.value)} placeholder="Authority" aria-label="Authority" className={inputCls} />
        <input value={form.keywords} onChange={(e) => set("keywords")(e.target.value)} placeholder="keywords, comma, separated" aria-label="Keywords" className={inputCls} />
        <input value={form.conflicts} onChange={(e) => set("conflicts")(e.target.value)} placeholder="conflicts with ids" aria-label="Conflicts with" className={inputCls} />
      </div>
      <button
        onClick={() => onSubmit(form)}
        disabled={!valid || busy}
        className="self-start rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
      >
        {busy ? "Saving…" : withId ? "Add clause" : "Save changes"}
      </button>
    </div>
  );
}

export default function ManageKbPage() {
  const { user, loading: authLoading } = useAuth();
  const router = useRouter();
  const [clauses, setClauses] = useState<KbClause[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [addKey, setAddKey] = useState(0);
  const [query, setQuery] = useState("");
  const [showAdd, setShowAdd] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setClauses(await apiGet<KbClause[]>("/kb"));
      setError(null);
    } catch (e) {
      setError(friendlyError(e).message);
    }
  }, []);

  // Initial load subscribes inline; updates land in the async callback.
  useEffect(() => {
    let cancelled = false;
    apiGet<KbClause[]>("/kb")
      .then((clauses) => {
        if (cancelled) return;
        setClauses(clauses);
        setError(null);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setError(friendlyError(e).message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const conflicts = useMemo(() => {
    const byId = new Map(clauses.map((c) => [c.id, c]));
    const pairs: [KbClause, KbClause][] = [];
    for (const c of clauses) {
      for (const other of c.conflicts_with ?? []) {
        const o = byId.get(other);
        if (o && c.id < o.id) pairs.push([c, o]);
      }
    }
    return pairs;
  }, [clauses]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return clauses;
    return clauses.filter((c) =>
      `${c.id} ${c.title} ${c.text} ${c.authority} ${(c.keywords ?? []).join(" ")}`.toLowerCase().includes(q),
    );
  }, [clauses, query]);

  const saveEdit = useCallback(
    async (id: string, form: typeof emptyForm) => {
      setBusy(true);
      setFormError(null);
      const payload: KbPayload = {
        title: form.title.trim(),
        text: form.text.trim(),
        authority: form.authority.trim(),
        keywords: csvToList(form.keywords),
        conflicts_with: csvToList(form.conflicts),
      };
      try {
        await updateClause(id, payload);
        setEditing(null);
        await refresh();
      } catch (e) {
        setFormError(friendlyError(e).message);
      } finally {
        setBusy(false);
      }
    },
    [refresh],
  );

  const addClause = useCallback(
    async (form: typeof emptyForm) => {
      setBusy(true);
      setFormError(null);
      try {
        await createClause({
          id: form.id.trim(),
          title: form.title.trim(),
          text: form.text.trim(),
          authority: form.authority.trim(),
          keywords: csvToList(form.keywords),
          conflicts_with: csvToList(form.conflicts),
        });
        await refresh();
        setAddKey((n) => n + 1);
        setShowAdd(false);
      } catch (e) {
        setFormError(friendlyError(e).message);
      } finally {
        setBusy(false);
      }
    },
    [refresh],
  );

  if (!authLoading && user && user.role !== "admin") {
    router.replace(user.role === "employee" ? "/help" : "/inbox");
    return null;
  }

  return (
    <main className="kb-workspace mx-auto flex min-h-0 w-full max-w-6xl min-w-0 flex-1 flex-col gap-4 overflow-x-hidden px-4 py-6">
      <div className="flex shrink-0 flex-wrap items-center gap-2">
        <div>
          <h1 className="text-xl font-bold tracking-tight">
            Knowledge base {clauses.length ? `(${clauses.length})` : ""}
          </h1>
          <p className="text-sm text-slate-500">Policy clauses the agent cites — edits apply immediately.</p>
        </div>
        <button
          onClick={() => setShowAdd((v) => !v)}
          className="ml-auto rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500"
        >
          {showAdd ? "Close" : "Add clause"}
        </button>
      </div>

      {error && (
        <div className="shrink-0">
          <InlineError message={error} />
        </div>
      )}

      {conflicts.length > 0 && (
        <div role="alert" className="shrink-0 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          <strong>Conflicting policy ({conflicts.length} {conflicts.length === 1 ? "pair" : "pairs"})</strong> — the agent refuses when both apply.{" "}
          {conflicts.map(([a, b]) => (
            <span key={`${a.id}-${b.id}`} className="mr-2 font-mono text-xs">{a.id} ↔ {b.id}</span>
          ))}
        </div>
      )}

      <div className="flex shrink-0 gap-2">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search clauses, keywords, authority…"
          aria-label="Search clauses"
          className="w-full max-w-md rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-900 placeholder:text-slate-400 focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100"
        />
      </div>

      {showAdd && (
        <section aria-label="Add clause" className="shrink-0 rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
          <h2 className="mb-2 text-sm font-semibold">New clause</h2>
          <ClauseForm key={`add-${addKey}`} initial={emptyForm} withId busy={busy} onSubmit={(f) => void addClause(f)} />
        </section>
      )}

      {formError && (
        <div className="shrink-0">
          <InlineError message={formError} />
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto pr-0.5 pb-1">
        <div className="grid content-start gap-3 md:grid-cols-2">
          {visible.map((c) => (
            <article key={c.id} className="rounded-lg border border-slate-200 bg-white p-3.5 shadow-sm">
              <div className="flex items-center justify-between gap-2">
                <span className="font-mono text-sm font-bold">{c.id}</span>
                <span className="flex gap-1.5">
                  {c.overridden && <Chip tone="amber">Overridden</Chip>}
                  <Chip>{c.authority}</Chip>
                </span>
              </div>
              <h2 className="mt-1.5 text-sm font-semibold">{c.title}</h2>
              <p className="text-xs text-slate-500">source: {c.source}</p>
              <p className="mt-2 text-sm leading-relaxed">{c.text}</p>
              {(c.keywords ?? []).length > 0 && (
                <p className="mt-1.5 text-xs text-slate-500">keywords: {c.keywords.join(", ")}</p>
              )}
              {(c.conflicts_with ?? []).length > 0 && (
                <p className="mt-1 text-xs text-amber-800">
                  conflicts with: {c.conflicts_with.join(", ")}
                </p>
              )}
              {editing === c.id ? (
                <div className="mt-3 border-t border-slate-200 pt-3">
                  <ClauseForm
                    key={c.id}
                    initial={{ id: c.id, title: c.title, text: c.text, authority: c.authority, keywords: (c.keywords ?? []).join(", "), conflicts: (c.conflicts_with ?? []).join(", ") }}
                    withId={false}
                    busy={busy}
                    onSubmit={(f) => void saveEdit(c.id, f)}
                  />
                  <button onClick={() => setEditing(null)} className="mt-2 text-xs text-slate-500 hover:underline">
                    Cancel
                  </button>
                </div>
              ) : (
                <button
                  onClick={() => { setEditing(c.id); setFormError(null); }}
                  className="mt-2.5 rounded-md border border-slate-300 bg-white px-2.5 py-1 text-xs text-slate-700 hover:bg-slate-100"
                >
                  Edit
                </button>
              )}
            </article>
          ))}
        </div>
        {visible.length === 0 && !error && (
          <p className="py-8 text-center text-sm text-slate-500">No clauses match your search.</p>
        )}
      </div>
    </main>
  );
}
