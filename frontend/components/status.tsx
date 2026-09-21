"use client";

/* Shared status chips and inline error helpers. */

export function Chip({
  tone = "zinc",
  children,
}: {
  tone?: "green" | "amber" | "blue" | "purple" | "red" | "zinc";
  children: React.ReactNode;
}) {
  const colors: Record<string, string> = {
    green: "border-green-200 bg-green-50 text-green-700",
    amber: "border-amber-200 bg-amber-50 text-amber-800",
    blue: "border-blue-200 bg-blue-50 text-blue-700",
    purple: "border-indigo-200 bg-indigo-50 text-indigo-700",
    red: "border-red-200 bg-red-50 text-red-700",
    zinc: "border-slate-200 bg-slate-50 text-slate-600",
  };
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${colors[tone]}`}
    >
      {children}
    </span>
  );
}

export function outcomeTone(outcome: string | null): "green" | "amber" | "blue" | "purple" | "zinc" {
  if (!outcome) return "zinc";
  if (outcome === "resolved") return "green";
  if (outcome === "escalated") return "amber";
  if (outcome === "ticket_raised") return "blue";
  if (outcome === "waiting_on_employee") return "purple";
  return "zinc";
}

export function outcomeLabel(outcome: string | null): string {
  if (!outcome) return "Open";
  if (outcome === "ticket_raised") return "Ticket raised";
  if (outcome === "waiting_on_employee") return "Waiting on you";
  return outcome.slice(0, 1).toUpperCase() + outcome.slice(1);
}

export function InlineError({ message }: { message: string }) {
  return (
    <p
      role="alert"
      className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700"
    >
      {message}
    </p>
  );
}

export function friendlyError(e: unknown): { message: string; status?: number } {
  const status =
    typeof e === "object" && e !== null && "status" in e && typeof (e as { status: unknown }).status === "number"
      ? (e as { status: number }).status
      : undefined;
  const raw = e instanceof Error ? e.message : "Something went wrong";
  if (status === 401) return { message: "Your session expired. Please sign in again.", status };
  if (status === 403) return { message: "You don't have permission to do that.", status };
  if (status === 404) return { message: "That conversation or record no longer exists.", status };
  if (status === 409) return { message: raw.replace(/^.*failed:\s*409\s*—\s*/, ""), status };
  return { message: raw, status };
}

export function snippet(text: string, n = 140): string {
  const t = (text ?? "").replace(/\s+/g, " ").trim();
  return t.length > n ? `${t.slice(0, n)}…` : t || "—";
}
