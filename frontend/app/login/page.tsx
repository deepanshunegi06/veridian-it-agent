"use client";

/* Sign-in page for Veridian IT Desk. Client-rendered so `next build` never
 * needs a live backend. Session state comes from the global <AuthProvider>
 * (mounted in app/layout.tsx): after login, returns to `?next=` (validated
 * same-origin) or a role-appropriate landing page. */

import { Suspense, useEffect, useRef, useState, type FormEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ApiError, defaultLandingForRole, isSafeNextPath } from "@/lib/auth";
import { SESSION_EXPIRED_MESSAGE } from "@/lib/session-expired";
import { useAuth } from "@/components/auth-provider";

function friendlyLoginError(err: unknown): string {
  if (
    err instanceof ApiError &&
    (err.status === 401 || err.status === 400 || err.status === 422)
  ) {
    return "Invalid email or password. Check your details and try again.";
  }
  return err instanceof Error ? err.message : "Sign-in failed. Try again.";
}

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { user, loading, login } = useAuth();
  const rawNext = searchParams.get("next");
  const next = isSafeNextPath(rawNext) ? (rawNext as string) : null;
  const expired = searchParams.get("expired") === "1";

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const redirected = useRef(false);

  // Authenticated users don't need this page (proxy also enforces this).
  useEffect(() => {
    if (!loading && user && !redirected.current) {
      redirected.current = true;
      router.replace(next ?? defaultLandingForRole(user.role));
    }
  }, [loading, user, next, router]);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const me = await login(email.trim(), password);
      redirected.current = true;
      router.replace(next ?? defaultLandingForRole(me.role));
      router.refresh();
    } catch (err) {
      setError(friendlyLoginError(err));
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return (
      <main className="mx-auto flex min-h-screen w-full max-w-md flex-col justify-center px-4 py-12">
        <p className="text-center text-sm text-slate-500">Checking session…</p>
      </main>
    );
  }

  return (
    <main className="mx-auto flex min-h-screen w-full max-w-md flex-col justify-center px-4 py-12">
      <div className="mb-6 text-center">
        <h1 className="text-xl font-bold tracking-tight text-slate-900">Veridian IT Desk</h1>
        <p className="mt-1 text-sm text-slate-500">
          Internal IT service agent — sign in to continue.
        </p>
      </div>

      <form
        onSubmit={onSubmit}
        className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm"
      >
        <div className="flex flex-col gap-4">
          {expired && (
            <p
              role="alert"
              className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800"
            >
              {SESSION_EXPIRED_MESSAGE}
            </p>
          )}
          <label className="flex flex-col gap-1 text-sm text-slate-900">
            <span className="font-medium">Email</span>
            <input
              type="email"
              name="email"
              required
              autoComplete="email"
              autoFocus
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@veridian.local"
              className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 outline-none placeholder:text-slate-400 focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100"
            />
          </label>

          <label className="flex flex-col gap-1 text-sm text-slate-900">
            <span className="font-medium">Password</span>
            <input
              type="password"
              name="password"
              required
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 outline-none placeholder:text-slate-400 focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100"
            />
          </label>

          {error && (
            <p
              role="alert"
              className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700"
            >
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={busy}
            className="rounded-md bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
          >
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </div>
      </form>

      <p className="mt-4 rounded-md border border-slate-200 bg-white px-3 py-2 text-center text-xs text-slate-500">
        Demo access uses backend-provisioned logins for the employee, IT agent,
        and admin roles.
      </p>
    </main>
  );
}

export default function LoginPage() {
  return (
      <Suspense
      fallback={
        <main className="mx-auto flex min-h-screen w-full max-w-md flex-col justify-center px-4 py-12">
          <p className="text-center text-sm text-slate-500">Loading…</p>
        </main>
      }
    >
      <LoginForm />
    </Suspense>
  );
}
