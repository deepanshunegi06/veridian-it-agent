"use client";

/* /manage/team — admin user management.
 * Real backend contract: GET/POST /auth/users, PATCH /auth/users/{id}.
 * Create requires name, email, role and password (min 8 chars). */

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  createUser,
  listUsers,
  updateUser,
  type ManagedUser,
} from "@/lib/api";
import { useAuth, type UserRole } from "@/components/auth-provider";
import { Chip, InlineError, friendlyError } from "@/components/status";

function normalize(u: Record<string, unknown>): ManagedUser {
  return {
    id: String(u.id ?? u.email ?? ""),
    email: String(u.email ?? ""),
    name: String(u.name ?? u.email ?? ""),
    role: String(u.role ?? "employee"),
    is_active: (u.is_active as boolean) !== false,
  };
}

export default function ManageTeamPage() {
  const { user, loading: authLoading } = useAuth();
  const router = useRouter();
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [form, setForm] = useState({
    name: "",
    email: "",
    role: "employee" as UserRole,
    password: "",
  });

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const raw = await listUsers();
      setUsers((raw as unknown as Record<string, unknown>[]).map(normalize));
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
    listUsers()
      .then((raw) => {
        if (cancelled) return;
        setUsers((raw as unknown as Record<string, unknown>[]).map(normalize));
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

  if (!authLoading && user && user.role !== "admin") {
    router.replace(user.role === "employee" ? "/help" : "/inbox");
    return null;
  }

  const mutate = async (id: string, action: "role" | "disable" | "enable", value?: string) => {
    setBusy(id + action);
    setError(null);
    setNotice(null);
    try {
      const patch =
        action === "role" ? { role: value! } :
        action === "disable" ? { is_active: false } : { is_active: true };
      await updateUser(id, patch);
      setNotice(`Updated ${id}.`);
      await refresh();
    } catch (e) {
      setError(friendlyError(e).message);
    } finally {
      setBusy(null);
    }
  };

  const create = async () => {
    const name = form.name.trim();
    const email = form.email.trim();
    if (!name || !email) {
      setError("Name and email are required.");
      return;
    }
    if (form.password.length < 8) {
      setError("A temporary password of at least 8 characters is required.");
      return;
    }
    setBusy("create");
    setError(null);
    setNotice(null);
    try {
      await createUser({ name, email, role: form.role, password: form.password });
      setNotice(`Invited ${email}.`);
      setForm({ name: "", email: "", role: "employee", password: "" });
      await refresh();
    } catch (e) {
      setError(friendlyError(e).message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <main className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-4 px-4 py-6">
      <div>
        <h1 className="text-xl font-bold tracking-tight">Team</h1>
        <p className="text-sm text-slate-500">Who can sign in, and what they can do.</p>
      </div>

      {error && <InlineError message={error} />}
      {notice && (
        <p className="rounded-lg border border-green-200 bg-green-50 px-3 py-2 text-sm text-green-800">
          {notice}
        </p>
      )}

      <section aria-label="Invite" className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-sm font-semibold">Invite teammate</h2>
        <div className="mt-2 grid gap-2 sm:grid-cols-[1fr_1fr_160px_1fr_auto]">
          <input value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} placeholder="Full name" aria-label="Full name" className="rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-900 placeholder:text-slate-400 focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100" />
          <input value={form.email} onChange={(e) => setForm((f) => ({ ...f, email: e.target.value }))} placeholder="Email" aria-label="Email" type="email" className="rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-900 placeholder:text-slate-400 focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100" />
          <select value={form.role} onChange={(e) => setForm((f) => ({ ...f, role: e.target.value as UserRole }))} aria-label="Role" className="rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-900">
            <option value="employee">Employee</option>
            <option value="it_agent">IT support</option>
            <option value="admin">Admin</option>
          </select>
          <input value={form.password} onChange={(e) => setForm((f) => ({ ...f, password: e.target.value }))} placeholder="Temporary password (min 8 chars)" aria-label="Temporary password" type="password" autoComplete="new-password" className="rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-900 placeholder:text-slate-400 focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100" />
          <button onClick={() => void create()} disabled={busy === "create"} className="rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50">
            {busy === "create" ? "Inviting…" : "Invite"}
          </button>
        </div>
      </section>

      <section aria-label="Members">
        {loading ? (
          <p className="py-8 text-center text-sm text-slate-500">Loading team…</p>
        ) : (
          <ol className="grid gap-2.5 md:grid-cols-2">
            {users.map((u) => (
              <li key={u.id} className={`rounded-lg border border-slate-200 p-3.5 shadow-sm ${u.is_active ? "bg-white" : "bg-slate-50 opacity-75"}`}>
                <div className="flex items-center gap-2.5">
                  <span className="flex h-9 w-9 items-center justify-center rounded-full bg-indigo-100 text-sm font-bold text-indigo-700">
                    {u.name.slice(0, 1).toUpperCase()}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-semibold">{u.name}</p>
                    <p className="truncate text-xs text-slate-500">{u.email}</p>
                  </div>
                  {!u.is_active && <Chip tone="red">Disabled</Chip>}
                </div>
                <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
                  <select
                    value={u.role}
                    disabled={!u.is_active || busy !== null}
                    onChange={(e) => void mutate(u.id, "role", e.target.value)}
                    aria-label={`Role for ${u.email}`}
                    className="rounded-md border border-slate-300 bg-white px-2 py-1 text-xs text-slate-700"
                  >
                    <option value="employee">Employee</option>
                    <option value="it_agent">IT support</option>
                    <option value="admin">Admin</option>
                  </select>
                  {u.is_active ? (
                    <button onClick={() => void mutate(u.id, "disable")} disabled={busy !== null} className="rounded-md border border-slate-300 bg-white px-2.5 py-1 text-xs text-slate-700 hover:bg-slate-100 disabled:opacity-50">
                      Disable
                    </button>
                  ) : (
                    <button onClick={() => void mutate(u.id, "enable")} disabled={busy !== null} className="rounded-md border border-slate-300 bg-white px-2.5 py-1 text-xs text-slate-700 hover:bg-slate-100 disabled:opacity-50">
                      Re-enable
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ol>
        )}
        {!loading && users.length === 0 && !error && (
          <p className="py-8 text-center text-sm text-slate-500">No team members found.</p>
        )}
      </section>
    </main>
  );
}
