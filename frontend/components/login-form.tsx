"use client";

/* Sign-in card for Veridian IT Desk. Email/password posts to the backend;
 * the three demo buttons use the passwordless dev-only demo-login endpoint
 * so no password ever ships to the browser. Session state comes from the
 * global <AuthProvider>: after login, returns to `?next=` (validated
 * same-origin) or a role-appropriate landing page. */

import { useEffect, useRef, useState, type ComponentType, type FormEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Field,
  FieldDescription,
  FieldGroup,
  FieldLabel,
  FieldSeparator,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import {
  ApiError,
  defaultLandingForRole,
  demoLogin,
  isSafeNextPath,
  type DemoRole,
} from "@/lib/auth";
import { SESSION_EXPIRED_MESSAGE } from "@/lib/session-expired";
import { useAuth } from "@/components/auth-provider";
import { AdminIcon, EmployeeIcon, ItAgentIcon } from "@/components/role-icons";

const DEMO_ROLES: { role: DemoRole; label: string; Icon: ComponentType<{ className?: string }> }[] = [
  { role: "employee", label: "Login as Employee", Icon: EmployeeIcon },
  { role: "it_agent", label: "Login as IT Agent", Icon: ItAgentIcon },
  { role: "admin", label: "Login as Admin", Icon: AdminIcon },
];

function friendlyLoginError(err: unknown): string {
  if (
    err instanceof ApiError &&
    (err.status === 401 || err.status === 400 || err.status === 422)
  ) {
    return "Invalid email or password. Check your details and try again.";
  }
  if (err instanceof ApiError && err.status === 404) {
    return "Demo sign-in is not enabled on this backend.";
  }
  return err instanceof Error ? err.message : "Sign-in failed. Try again.";
}

export function LoginForm({ className, ...props }: React.ComponentProps<"div">) {
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
  const [demoBusy, setDemoBusy] = useState<DemoRole | null>(null);
  const redirected = useRef(false);

  // Authenticated users don't need this page (proxy also enforces this).
  useEffect(() => {
    if (!loading && user && !redirected.current) {
      redirected.current = true;
      router.replace(next ?? defaultLandingForRole(user.role));
    }
  }, [loading, user, next, router]);

  function land(me: { role: "employee" | "it_agent" | "admin" }) {
    redirected.current = true;
    router.replace(next ?? defaultLandingForRole(me.role));
    router.refresh();
  }

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (busy || demoBusy) return;
    setBusy(true);
    setError(null);
    try {
      land(await login(email.trim(), password));
    } catch (err) {
      setError(friendlyLoginError(err));
    } finally {
      setBusy(false);
    }
  }

  async function onDemo(role: DemoRole) {
    if (busy || demoBusy) return;
    setDemoBusy(role);
    setError(null);
    try {
      land(await demoLogin(role));
    } catch (err) {
      setError(friendlyLoginError(err));
    } finally {
      setDemoBusy(null);
    }
  }

  if (loading) {
    return (
      <div className={cn("flex flex-col gap-6", className)} {...props}>
        <Card>
          <CardContent>
            <p className="py-4 text-center text-sm text-slate-500">Checking session…</p>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className={cn("flex flex-col gap-6", className)} {...props}>
      <Card>
        <CardHeader>
          <CardTitle>Welcome back</CardTitle>
          <CardDescription>Sign in to the Veridian IT support workspace</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit}>
            <FieldGroup>
              {expired && (
                <p
                  role="alert"
                  className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800"
                >
                  {SESSION_EXPIRED_MESSAGE}
                </p>
              )}
              {error && (
                <p
                  role="alert"
                  className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700"
                >
                  {error}
                </p>
              )}
              <Field>
                <FieldLabel htmlFor="email">Email</FieldLabel>
                <Input
                  id="email"
                  type="email"
                  placeholder="you@veridian.local"
                  required
                  autoComplete="email"
                  autoFocus
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />
              </Field>
              <Field>
                <FieldLabel htmlFor="password">Password</FieldLabel>
                <Input
                  id="password"
                  type="password"
                  required
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </Field>
              <Field>
                <Button type="submit" disabled={busy || demoBusy !== null}>
                  {busy ? "Signing in…" : "Login"}
                </Button>
              </Field>
              <FieldSeparator>Or continue as</FieldSeparator>
              <Field>
                {DEMO_ROLES.map(({ role, label, Icon }) => (
                  <Button
                    key={role}
                    variant="outline"
                    type="button"
                    onClick={() => onDemo(role)}
                    disabled={busy || demoBusy !== null}
                  >
                    <Icon className="size-4" />
                    {demoBusy === role ? "Signing in…" : label}
                  </Button>
                ))}
                <FieldDescription className="text-center">
                  Demo sign-in uses backend-provisioned logins; no password needed.
                </FieldDescription>
              </Field>
            </FieldGroup>
          </form>
        </CardContent>
      </Card>
      <FieldDescription className="px-6 text-center">
        By signing in, you agree to our <a href="/terms">Terms of Service</a> and{" "}
        <a href="/privacy">Privacy Policy</a>.
      </FieldDescription>
    </div>
  );
}
