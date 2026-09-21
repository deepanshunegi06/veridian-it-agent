"use client";

/* Employee ticket details panel (right pane of /help).
 * Shows only employee-appropriate fields: ticket number, status, created /
 * assignment info, support-state chip, and a reassurance note. Internal AI
 * audit details (tools, citations, refusals) are deliberately not rendered
 * here. */

import { ticketStatusLabel, type ThreadDetail } from "@/lib/api";
import { Chip } from "@/components/status";

function statusTone(status: string): "green" | "amber" | "blue" | "zinc" {
  if (status === "Resolved") return "green";
  if (status === "In progress") return "blue";
  if (status === "Waiting") return "amber";
  return "zinc";
}

/* Support-state chip derived from current detail state (never inferred from
 * ticket creation). Pending handoff: outcome case-insensitive `escalated`
 * with no `assigned_to`/`assigned_to_user_id`. Assigned: any IT ownership
 * (non-empty `assigned_to` or `assigned_to_user_id`). Otherwise no
 * support-state message. Backend messages are never mutated here. */
function isPendingHandoff(detail: ThreadDetail | null | undefined): boolean {
  if (!detail) return false;
  const outcome =
    typeof detail.outcome === "string" ? detail.outcome.toLowerCase() : "";
  if (outcome !== "escalated") return false;
  const owner =
    typeof detail.assigned_to === "string"
      ? detail.assigned_to.trim()
      : detail.assigned_to;
  if (owner) return false;
  const ownerId = detail.assigned_to_user_id;
  if (typeof ownerId === "string" ? ownerId.trim() : ownerId) return false;
  return true;
}

function isAssignedToIT(detail: ThreadDetail | null | undefined): boolean {
  if (!detail) return false;
  const owner =
    typeof detail.assigned_to === "string"
      ? detail.assigned_to.trim()
      : detail.assigned_to;
  if (owner) return true;
  const ownerId = detail.assigned_to_user_id;
  if (typeof ownerId === "string" ? ownerId.trim() : ownerId) return true;
  return false;
}

export function TicketDetails({
  detail,
  ticketId,
  liveTicketId,
}: {
  detail: ThreadDetail | null;
  ticketId: string | null;
  liveTicketId: string | null;
}) {
  // ticketId is already the resolved fallback (detail -> summary -> map ->
  // live) from the parent; liveTicketId is kept as a final safety net so a
  // known ticket number stays visible even if the detail refresh races.
  const ticket = ticketId ?? liveTicketId;
  const status = ticketStatusLabel(detail);
  const created = detail?.messages?.[0]?.at ?? null;
  const createdLabel = created
    ? new Date(created).toLocaleString(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      })
    : null;
  const pending = isPendingHandoff(detail);
  const assigned = !pending && isAssignedToIT(detail);
  const supportMessage = pending
    ? "Someone from the IT department will connect shortly."
    : assigned
      ? "IT support is now helping with this ticket."
      : null;

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <h2 className="text-sm font-semibold text-slate-900">Ticket details</h2>
      {!detail && !ticket ? (
        <p className="mt-2 text-sm text-slate-500">
          Start a new ticket or select one from your list to see its details
          here.
        </p>
      ) : !ticket ? (
        <div className="mt-2 space-y-2">
          <p className="text-sm text-slate-500">Ticket details unavailable.</p>
          <dl className="space-y-1.5 text-sm text-slate-900">
            <div className="flex items-center justify-between gap-2">
              <dt className="text-slate-500">Status</dt>
              <dd>
                <Chip tone={statusTone(status)}>{status}</Chip>
              </dd>
            </div>
            <div className="flex items-center justify-between gap-2">
              <dt className="text-slate-500">Opened</dt>
              <dd className="truncate text-right text-slate-900">{createdLabel ?? "—"}</dd>
            </div>
            <div className="flex items-center justify-between gap-2">
              <dt className="text-slate-500">Assigned to</dt>
              <dd className="truncate text-right text-slate-900">
                {detail?.assigned_to ?? "IT support queue"}
              </dd>
            </div>
          </dl>
        </div>
      ) : (
        <dl className="mt-2 space-y-1.5 text-sm text-slate-900">
          <div className="flex items-center justify-between gap-2">
            <dt className="text-slate-500">Ticket</dt>
            <dd className="font-mono text-sm font-bold text-slate-900">{ticket}</dd>
          </div>
          <div className="flex items-center justify-between gap-2">
            <dt className="text-slate-500">Status</dt>
            <dd>
              <Chip tone={statusTone(status)}>{status}</Chip>
            </dd>
          </div>
          <div className="flex items-center justify-between gap-2">
            <dt className="text-slate-500">Opened</dt>
            <dd className="truncate text-right text-slate-900">{createdLabel ?? "—"}</dd>
          </div>
          <div className="flex items-center justify-between gap-2">
            <dt className="text-slate-500">Assigned to</dt>
            <dd className="truncate text-right text-slate-900">
              {detail?.assigned_to ?? "IT support queue"}
            </dd>
          </div>
        </dl>
      )}
      {supportMessage && (
        <div className="mt-3">
          <Chip tone={pending ? "amber" : "blue"}>{supportMessage}</Chip>
        </div>
      )}
      <p className="mt-3 border-t border-slate-200 pt-3 text-xs text-slate-500">
        Tickets are reviewed by IT support — you&apos;ll see replies here as
        soon as someone picks yours up.
      </p>
    </div>
  );
}
