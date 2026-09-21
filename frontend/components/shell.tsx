"use client";

/* Professional responsive shell: sidebar (desktop) + drawer (mobile) + top
 * bar. Role-aware nav, active route highlight, account identity + logout.
 * No demo/debug links. */

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useAuth, type UserRole } from "@/components/auth-provider";
import { apiGet, type Ticket } from "@/lib/api";

interface NavItem {
  href: string;
  label: string;
  roles: UserRole[];
  match: (path: string) => boolean;
}

const NAV: NavItem[] = [
  {
    href: "/help",
    label: "Help",
    roles: ["employee"],
    match: (p) => p === "/help" || p.startsWith("/help/"),
  },
  {
    href: "/inbox",
    label: "Inbox",
    roles: ["it_agent", "admin"],
    match: (p) => p === "/inbox" || p.startsWith("/inbox/"),
  },
  {
    href: "/manage/kb",
    label: "Manage KB",
    roles: ["admin"],
    match: (p) => p.startsWith("/manage/kb"),
  },
  {
    href: "/manage/team",
    label: "Team",
    roles: ["admin"],
    match: (p) => p.startsWith("/manage/team"),
  },
];

function roleLabel(role: UserRole): string {
  if (role === "admin") return "Admin";
  if (role === "it_agent") return "IT support";
  return "Employee";
}

export function Shell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { user, loading, logout } = useAuth();
  const [drawer, setDrawer] = useState(false);
  const [roleMenu, setRoleMenu] = useState(false);
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [ticketsError, setTicketsError] = useState<string | null>(null);

  const role: UserRole = user?.role ?? "employee";
  const items = NAV.filter((n) => n.roles.includes(role));
  const showTickets = user?.role === "it_agent" || user?.role === "admin";

  // Tickets are a global support queue, not per-ticket context: list them
  // below the sidebar nav (desktop + mobile drawer). Auth-aware fetch; errors
  // stay inline so navigation never breaks.
  useEffect(() => {
    if (!showTickets) return;
    let cancelled = false;
    apiGet<Ticket[]>("/tickets")
      .then((t) => {
        if (cancelled) return;
        setTickets(Array.isArray(t) ? t : []);
        setTicketsError(null);
      })
      .catch(() => {
        if (cancelled) return;
        setTickets([]);
        setTicketsError("Couldn't load tickets.");
      });
    return () => {
      cancelled = true;
    };
  }, [showTickets]);

  // Only linkable rows (with request_id) so each row goes to /inbox/{id};
  // deduplicate by ticket id for a stable global queue.
  const queueTickets = useMemo(() => {
    const seen = new Set<string>();
    return tickets.filter((t) => {
      if (!t.request_id || seen.has(t.id)) return false;
      seen.add(t.id);
      return true;
    });
  }, [tickets]);
  // One support destination: /inbox with in-page Mine/Open/Unassigned/Resolved
  // filters (see ?filter=mine). No duplicate "My assigned" route concept.
  const isActive = (item: NavItem): boolean => item.match(pathname);

  const ticketList = showTickets ? (
    <section aria-label="Tickets" className="border-t border-slate-200 px-3 py-2">
      <h2 className="px-1 py-1 text-[11px] font-semibold tracking-wide text-slate-500 uppercase">
        Tickets
      </h2>
      {ticketsError ? (
        <p className="px-1 py-1 text-xs text-slate-500">{ticketsError}</p>
      ) : queueTickets.length === 0 ? (
        <p className="px-1 py-1 text-xs text-slate-500">No tickets yet.</p>
      ) : (
        <ul className="max-h-64 space-y-1 overflow-y-auto pr-0.5">
          {queueTickets.map((t) => {
            const href = `/inbox/${encodeURIComponent(t.request_id!)}`;
            const active = pathname === href;
            const status = t.ticket_status ?? t.status;
            return (
              <li key={t.id}>
                <Link
                  href={href}
                  aria-current={active ? "page" : undefined}
                  onClick={() => setDrawer(false)}
                  className={`block truncate rounded-md border px-2 py-1.5 transition-colors ${
                    active
                      ? "border-indigo-200 bg-indigo-50"
                      : "border-slate-200 bg-slate-50 hover:bg-slate-100"
                  }`}
                  title={`${t.id} — ${t.summary ?? t.status}`}
                >
                  <span className="block truncate font-mono text-[11px] font-bold text-slate-900">
                    {t.id}
                  </span>
                  <span className="block truncate text-xs text-slate-600">
                    {t.summary ?? t.status}
                  </span>
                  <span className="mt-0.5 block truncate text-[11px] text-slate-500">
                    {t.employee ? `${t.employee} · ` : ""}
                    {status}
                    {t.open ? "" : " · closed"}
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  ) : null;

  const closeAndGo = (href: string) => {
    setDrawer(false);
    router.push(href);
  };

  // /help is the single employee workspace with its own left rail (Help
  // heading, New ticket, Your tickets). The global product sidebar would be a
  // second, competing rail, so it stays hidden for employees there. The
  // authenticated top bar + account menu above remain visible.
  const hideSidebar = role === "employee" && pathname.startsWith("/help");

  const navList = (
    <nav aria-label="Primary" className="flex flex-col gap-1 p-3">
      {items.map((item) => {
        const active = isActive(item);
        return (
          <Link
            key={item.href + item.label}
            href={item.href}
            aria-current={active ? "page" : undefined}
            onClick={() => setDrawer(false)}
            className={`rounded-md px-3 py-2 text-sm font-medium transition-colors ${
              active
                ? "bg-indigo-50 text-indigo-700"
                : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
            }`}
          >
            {item.label}
          </Link>
        );
      })}
    </nav>
  );

  // Authentication is a separate surface, not another workspace view. Do not
  // show the product navigation, account menu, or a signed-out "Sign in"
  // button around the login form.
  if (pathname === "/login") return <>{children}</>;

  // While the session is unknown, show a neutral brand frame instead of the
  // product chrome: the sidebar and account menu would otherwise flash with
  // signed-out defaults on every cold load (including the / landing bounce
  // to /login).
  if (loading) {
    return (
      <div className="flex min-h-screen flex-col bg-slate-50 text-slate-900">
        <header className="shrink-0 border-b border-slate-200 bg-white/95">
          <div className="mx-auto flex h-14 w-full max-w-7xl items-center gap-2 px-4">
            <span className="flex h-7 w-7 items-center justify-center rounded-md bg-indigo-600 text-xs font-bold text-white">
              V
            </span>
            <span className="text-sm font-bold tracking-tight text-slate-900">
              Veridian IT Desk
            </span>
          </div>
        </header>
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">{children}</div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen flex-col bg-slate-50 text-slate-900">
      {/* Top bar */}
      <header className="sticky top-0 z-30 shrink-0 border-b border-slate-200 bg-white/95 backdrop-blur">
        <div className="mx-auto flex h-14 w-full max-w-7xl items-center gap-3 px-4">
          {!hideSidebar && (
            <button
              className="rounded-md p-2 hover:bg-slate-100 md:hidden"
              aria-label="Open navigation"
              onClick={() => setDrawer(true)}
            >
              <span aria-hidden className="block h-4 w-5 space-y-1">
                <span className="block h-0.5 bg-current" />
                <span className="block h-0.5 bg-current" />
                <span className="block h-0.5 bg-current" />
              </span>
            </button>
          )}
          <Link href="/" className="flex items-center gap-2">
            <span className="flex h-7 w-7 items-center justify-center rounded-md bg-indigo-600 text-xs font-bold text-white">
              V
            </span>
            <span className="text-sm font-bold tracking-tight text-slate-900">Veridian IT Desk</span>
          </Link>
          <div className="ml-auto flex items-center gap-2">
            {loading ? (
              <span className="text-xs text-slate-500">Signing in…</span>
            ) : user ? (
              <div className="relative">
                <button
                  onClick={() => setRoleMenu((v) => !v)}
                  aria-haspopup="menu"
                  aria-expanded={roleMenu}
                  className="flex items-center gap-2 rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-left hover:bg-slate-50"
                >
                  <span className="flex h-7 w-7 items-center justify-center rounded-full bg-indigo-100 text-xs font-bold text-indigo-700">
                    {user.name.slice(0, 1).toUpperCase()}
                  </span>
                  <span className="hidden sm:block">
                    <span className="block max-w-36 truncate text-xs font-semibold leading-tight">
                      {user.name}
                    </span>
                    <span className="block text-[11px] leading-tight text-slate-500">
                      {roleLabel(user.role)}
                    </span>
                  </span>
                </button>
                {roleMenu && (
                  <div
                    role="menu"
                    className="absolute right-0 mt-2 w-60 rounded-lg border border-slate-200 bg-white p-2 shadow-md"
                    onMouseLeave={() => setRoleMenu(false)}
                  >
                    <p className="truncate px-2 py-1 text-xs text-slate-500">{user.email}</p>
                    <div className="my-1 border-t border-slate-200" />
                    <button
                      role="menuitem"
                      onClick={() => {
                        setRoleMenu(false);
                        void logout().then(() => router.push("/login"));
                      }}
                      className="block w-full rounded-md px-2 py-1.5 text-left text-sm text-red-600 hover:bg-red-50"
                    >
                      Log out
                    </button>
                  </div>
                )}
              </div>
            ) : (
              <Link
                href="/login"
                className="rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500"
              >
                Sign in
              </Link>
            )}
          </div>
        </div>
      </header>

      <div className="mx-auto flex min-h-0 w-full max-w-7xl flex-1 items-stretch gap-0">
        {/* Desktop sidebar (hidden for employees inside /help: the workspace
            brings its own left rail). */}
        {!hideSidebar && (
          <aside className="hidden w-60 shrink-0 border-r border-slate-200 bg-white md:block">
            <div className="sticky top-14 flex max-h-[calc(100vh-3.5rem)] flex-col overflow-y-auto">
              {navList}
              {ticketList}
            </div>
          </aside>
        )}

        {/* Mobile drawer (not rendered where the sidebar is hidden). */}
        {!hideSidebar && drawer && (
          <div className="fixed inset-0 z-40 md:hidden" role="dialog" aria-modal="true" aria-label="Navigation">
            <div className="absolute inset-0 bg-black/40" onClick={() => setDrawer(false)} />
            <div className="absolute top-0 left-0 flex h-full w-72 flex-col bg-white shadow-xl">
              <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
                <span className="text-sm font-bold text-slate-900">Veridian IT Desk</span>
                <button
                  aria-label="Close navigation"
                  onClick={() => setDrawer(false)}
                  className="rounded-md px-2 py-1 text-sm text-slate-600 hover:bg-slate-100"
                >
                  ✕
                </button>
              </div>
              <div className="flex-1 overflow-y-auto">
                {items.map((item) => (
                  <button
                    key={item.href + item.label}
                    onClick={() => closeAndGo(item.href)}
                    className="block w-full px-4 py-2.5 text-left text-sm text-slate-700 hover:bg-slate-100"
                  >
                    {item.label}
                  </button>
                ))}
                {ticketList}
              </div>
              <div className="border-t border-slate-200 p-4">
                {user && (
                  <button
                    onClick={() => {
                      setDrawer(false);
                      void logout().then(() => router.push("/login"));
                    }}
                    className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-100"
                  >
                    Log out ({user.email})
                  </button>
                )}
              </div>
            </div>
          </div>
        )}

        {/* Main */}
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">{children}</div>
      </div>
    </div>
  );
}
