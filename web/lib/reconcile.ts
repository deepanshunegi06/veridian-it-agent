import type { ThreadMessageLike } from "@assistant-ui/react";

/** Stable canonical id for a backend message seq (`m-{seq}` on the wire). */
export function canonicalId(seq: number): string {
  return `m-${seq}`;
}

/** Temporary client-side bubbles created while streaming (`local-*`). */
export function isLocalId(id: string): boolean {
  return id.startsWith("local-");
}

/**
 * Append `incoming` only when its stable id is absent.
 * Dedups strictly by id (backend `m-{seq}`), never by text: repeated text
 * can be a legitimate repeat (e.g. the employee sends the same words twice
 * and gets the same follow-up twice), so text equality must not collapse.
 */
export function appendById(
  prev: ThreadMessageLike[],
  incoming: ThreadMessageLike,
): ThreadMessageLike[] {
  return prev.some((m) => m.id === incoming.id) ? prev : [...prev, incoming];
}

/**
 * Reconcile a temporary streamed bubble (`local-a-*`) with its canonical
 * backend message (`m-{seq}`). When the `say` SSE event carries `seq`, the
 * streamer uses the canonical id up front so this is a no-op; when the
 * canonical message arrives first via the live subscription, the pending
 * local bubble is dropped in favour of the canonical one.
 *
 * Pure and deterministic: no text comparison, only stable ids.
 */
export function reconcileLocalToCanonical(
  prev: ThreadMessageLike[],
  localId: string,
  canonical: ThreadMessageLike,
): ThreadMessageLike[] {
  if (!isLocalId(localId)) return appendById(prev, canonical);
  if (prev.some((m) => m.id === canonical.id)) {
    // Canonical already present (live subscription won the race): drop local.
    return prev.filter((m) => m.id !== localId);
  }
  // Streamed first: rename the pending bubble to the canonical id.
  let renamed = false;
  const out = prev.map((m) => {
    if (m.id === localId) {
      renamed = true;
      return { ...m, id: canonical.id };
    }
    return m;
  });
  return renamed ? out : appendById(out, canonical);
}

/** Extract a numeric `seq` from a `say` SSE payload, if the backend sent one. */
export function seqFromSayData(data: unknown): number | null {
  if (data && typeof data === "object") {
    const d = data as Record<string, unknown>;
    if (typeof d.seq === "number" && Number.isInteger(d.seq) && d.seq >= 0) {
      return d.seq;
    }
  }
  return null;
}
