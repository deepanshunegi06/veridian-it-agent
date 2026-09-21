"use client";

/* Global session state for the Veridian IT Desk UI.
 *
 * Mounted once in `app/layout.tsx`: <AuthProvider><Shell>{children}</Shell>.
 * Read anywhere via useAuth() -> { user, loading, login, logout, refresh }.
 *
 * Rules:
 * - Backend (cookie `veridian_access` + GET /auth/me) is authoritative.
 * - Expired sessions navigate once via `@/lib/session-expired`
 *   (`/login?next=<current>&expired=1`); the provider itself never calls
 *   navigation APIs, it only clears `user` so state stays consistent after
 *   expiry (including via the `veridian:session-expired` event fired before
 *   navigation).
 * - `logout()` never rejects (safe to call fire-and-forget from menus) and
 *   never touches `window.location`; routes/proxy own post-logout routing.
 * - `UserRole`/`SessionUser` aliases stay exported for existing consumers.
 * - `switchRole` is intentionally unprovided: roles come from the backend
 *   session, so client-side role preview cannot exist under real auth.
 */

import Link from "next/link";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  fetchMe,
  login as loginRequest,
  logout as logoutRequest,
  type Role,
  type User,
} from "@/lib/auth";
import { SESSION_EXPIRED_EVENT } from "@/lib/session-expired";

export type UserRole = Role;
export type SessionUser = User;

export interface AuthContextValue {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<User>;
  logout: () => Promise<void>;
  refresh: () => Promise<User | null>;
  /** Not provided under backend auth (roles are server-issued). */
  switchRole?: (role: UserRole) => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const refresh = useCallback(async (): Promise<User | null> => {
    try {
      const me = await fetchMe();
      if (mounted.current) {
        setUser(me);
        setLoading(false);
      }
      return me;
    } catch {
      // Anonymous (401) or backend down: report null. A 401 from fetchMe
      // already triggered the single centralized redirect to
      // /login?next=…&expired=1 (no-op on /login itself), so no inline
      // session message is set here — the login alert is the primary UX.
      if (mounted.current) {
        setUser(null);
        setLoading(false);
      }
      return null;
    }
  }, []);

  // Clear stale identity immediately when any API/SSE helper reports expiry,
  // before the single navigation to /login lands. Keeps context consistent
  // even if navigation is still in flight.
  useEffect(() => {
    const onExpired = () => {
      if (mounted.current) setUser(null);
    };
    window.addEventListener(SESSION_EXPIRED_EVENT, onExpired);
    return () => {
      window.removeEventListener(SESSION_EXPIRED_EVENT, onExpired);
    };
  }, []);

  // Initial session load. setState runs only in async continuations, so this
  // stays a pure subscription-style effect (no cascading-render lint).
  useEffect(() => {
    let cancelled = false;
    void fetchMe().then(
      (me) => {
        if (!cancelled && mounted.current) {
          setUser(me);
          setLoading(false);
        }
      },
      () => {
        if (!cancelled && mounted.current) {
          setUser(null);
          setLoading(false);
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(
    async (email: string, password: string): Promise<User> => {
      // Throws ApiError on bad credentials so forms can show an error state.
      const u = await loginRequest(email, password);
      if (mounted.current) {
        setUser(u);
        setLoading(false);
      }
      return u;
    },
    [],
  );

  const logout = useCallback(async (): Promise<void> => {
    try {
      await logoutRequest();
    } catch {
      // Backend unreachable: local session is still cleared below.
    } finally {
      if (mounted.current) setUser(null);
    }
  }, []);

  const value = useMemo(
    () => ({ user, loading, login, logout, refresh }),
    [user, loading, login, logout, refresh],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within <AuthProvider>");
  return ctx;
}

/** UX-only gate: hides children until session (and optionally role) checks pass. */
export function AuthGate({
  allow,
  children,
  fallback,
}: {
  allow?: Role[];
  children: ReactNode;
  fallback?: ReactNode;
}) {
  const { user, loading } = useAuth();

  if (loading) {
    return <p className="px-3 py-6 text-sm text-zinc-500">Checking session…</p>;
  }
  if (!user) {
    return (
      fallback ?? (
        <p className="px-3 py-6 text-sm text-zinc-600 dark:text-zinc-400">
          You need to{" "}
          <Link
            href="/login"
            className="text-blue-600 hover:underline dark:text-blue-400"
          >
            sign in
          </Link>{" "}
          to view this page.
        </p>
      )
    );
  }
  if (allow && !allow.includes(user.role)) {
    return (
      fallback ?? (
        <p className="rounded-md border border-amber-500 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:bg-amber-950 dark:text-amber-200">
          Your role ({user.role}) is not authorized for this page.
        </p>
      )
    );
  }
  return <>{children}</>;
}
