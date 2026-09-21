"use client";

/* Role-aware landing redirect:
 * employee -> /help, it_agent/admin -> /inbox, signed out -> /login. */

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/components/auth-provider";

export default function LandingPage() {
  const { user, loading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (loading) return;
    if (!user) {
      router.replace("/login");
      return;
    }
    if (user.role === "employee") router.replace("/help");
    else router.replace("/inbox");
  }, [loading, router, user]);

  return (
    <main className="flex flex-1 items-center justify-center px-4 py-16">
      <div className="flex flex-col items-center gap-3 text-center">
        <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-zinc-900 text-sm font-bold text-white dark:bg-zinc-100 dark:text-zinc-900">
          V
        </span>
        <p className="text-sm text-zinc-500">
          {loading ? "Signing you in…" : "Taking you to your workspace…"}
        </p>
      </div>
    </main>
  );
}
