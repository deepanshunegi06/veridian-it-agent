"use client";

/* Role-aware landing redirect:
 * employee -> /help, it_agent/admin -> /inbox, signed out -> /login.
 * Renders nothing while deciding: any placeholder here flashes inside the
 * app frame on every cold load before the redirect lands. */

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

  return null;
}
