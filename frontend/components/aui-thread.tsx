"use client";

/* Shared assistant-ui chat UI.
 * Pattern per https://www.assistant-ui.com/docs/runtimes/custom/external-store:
 * pages own the message array and pass an `onNew` handler that POSTs to the
 * backend; this file renders the Thread via assistant-ui primitives. */

import {
  AssistantRuntimeProvider,
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useAuiState,
  useMessagePartText,
  type AppendMessage,
  type AssistantRuntime,
  type ThreadMessageLike,
} from "@assistant-ui/react";
import type { BackendMessage } from "@/lib/api";
import { TypingIndicator } from "@/components/assistant-ui/elements/typing-indicator";

function TextPart() {
  const { text } = useMessagePartText();
  return <span className="break-words whitespace-pre-wrap">{text}</span>;
}

const partComponents = { Text: TextPart };

/** Sender label for incoming bubbles. Reads `metadata.custom.sender` set by
 * `backendToAui`; renders nothing for own/outgoing messages (no sender) or
 * when the backend sent no name. Never alters message text. */
function SenderLabel() {
  const sender = useAuiState(
    (s) =>
      (s.message.metadata?.custom as Record<string, unknown> | undefined)
        ?.sender as string | undefined,
  );
  if (!sender) return null;
  return (
    <span className="mb-1 block text-[11px] font-medium text-slate-500">
      {sender}
    </span>
  );
}

function UserMessage() {
  return (
    <MessagePrimitive.Root className="flex justify-end">
      <div className="max-w-[85%] rounded-lg bg-indigo-600 px-3 py-2 text-sm text-white shadow-sm">
        <MessagePrimitive.Parts components={partComponents} />
      </div>
    </MessagePrimitive.Root>
  );
}

function AssistantMessage() {
  return (
    <MessagePrimitive.Root className="flex justify-start">
      <div className="max-w-[85%]">
        <SenderLabel />
        <div className="empty:hidden rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm">
          <MessagePrimitive.Parts components={partComponents} />
        </div>
      </div>
    </MessagePrimitive.Root>
  );
}

function SystemMessage() {
  return (
    <MessagePrimitive.Root className="flex justify-center">
      <div className="max-w-[90%] rounded-md border border-dashed border-slate-300 bg-slate-50 px-3 py-1.5 text-center text-xs text-slate-500">
        <MessagePrimitive.Parts components={partComponents} />
      </div>
    </MessagePrimitive.Root>
  );
}

/** Animated run indicator inside the transcript, per the assistant-ui
 * TypingIndicator pattern: visible only while a run is active and no
 * assistant content has arrived yet. External-store runs also spend time
 * with the viewer's own message last (network + backend work before the
 * first token), so that counts as waiting too; the first streamed part (or
 * run end) unmounts the indicator. Shared by the employee and IT threads. */
function AssistantTyping() {
  const waiting = useAuiState((s) => {
    if (!s.thread.isRunning) return false;
    const last = s.thread.messages.at(-1);
    if (!last) return true;
    if (last.role === "user") return true;
    return last.role === "assistant" && last.parts.length === 0;
  });
  if (!waiting) return null;
  return <TypingIndicator />;
}

function Composer({
  placeholder,
  disabled,
  showStop = true,
}: {
  placeholder: string;
  disabled?: boolean;
  /** When true (default), an active AI run swaps the Send button for a
   * Stop (Cancel) control in the same action slot. Pages whose `isRunning`
   * is not an AI generation (e.g. IT human-reply submitting state) pass
   * `false` so the slot always shows Send. */
  showStop?: boolean;
}) {
  const isRunning = useAuiState((s) => s.thread.isRunning);
  const showCancel = showStop && isRunning;
  const inputLocked = Boolean(disabled) || isRunning;
  const sendLocked = Boolean(disabled) || isRunning;
  return (
    <div className="shrink-0 border-t border-slate-200 bg-white p-3">
      <ComposerPrimitive.Root className="flex items-end gap-2 rounded-2xl border border-slate-200 bg-slate-100 p-2 pl-4 transition focus-within:border-indigo-500 focus-within:bg-white focus-within:ring-2 focus-within:ring-indigo-100">
        <ComposerPrimitive.Input
          placeholder={placeholder}
          aria-label="Message"
          disabled={inputLocked}
          className="max-h-36 min-h-11 flex-1 resize-none bg-transparent px-1 py-2.5 text-sm text-slate-900 outline-none placeholder:text-slate-400 disabled:cursor-not-allowed disabled:opacity-60"
        />
        {/* Single action slot: Send when idle, Stop (Cancel) while an AI
         * run is active. Never both at once. */}
        {showCancel ? (
          <ComposerPrimitive.Cancel
            aria-label="Stop generating"
            className="flex h-9 shrink-0 items-center justify-center rounded-full bg-indigo-600 px-4 text-sm font-medium text-white shadow-sm transition hover:bg-indigo-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Stop
          </ComposerPrimitive.Cancel>
        ) : (
          <ComposerPrimitive.Send
            aria-label="Send message"
            disabled={sendLocked ? true : undefined}
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-indigo-600 text-white shadow-sm transition hover:bg-indigo-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <svg
              aria-hidden="true"
              viewBox="0 0 16 16"
              fill="none"
              className="h-4 w-4"
            >
              <path
                d="M8 13V3m0 0L3.5 7.5M8 3l4.5 4.5"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </ComposerPrimitive.Send>
        )}
      </ComposerPrimitive.Root>
    </div>
  );
}

export function ChatThread({
  runtime,
  welcome,
  placeholder = "Type a message…",
  composerDisabled = false,
  showStop = true,
  allowCancel = true,
}: {
  runtime: AssistantRuntime;
  welcome: string;
  placeholder?: string;
  composerDisabled?: boolean;
  /** Show the Stop (Cancel) control in the action slot while an AI run is
   * active. Defaults to true (employee AI thread). The IT inbox passes
   * `false` because its `isRunning` is human-reply submitting state, not
   * an AI generation. */
  showStop?: boolean;
  /** Alias for `showStop`: either prop set to `false` hides Stop. */
  allowCancel?: boolean;
}) {
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ThreadPrimitive.Root className="flex h-full min-h-0 flex-1 flex-col overflow-hidden">
        <ThreadPrimitive.Viewport className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-4 py-4">
          <ThreadPrimitive.Empty>
            <p className="mx-auto max-w-md py-10 text-center text-sm text-slate-500">
              {welcome}
            </p>
          </ThreadPrimitive.Empty>
          <ThreadPrimitive.Messages
            components={{
              UserMessage,
              AssistantMessage,
              SystemMessage,
            }}
          />
          <AssistantTyping />
        </ThreadPrimitive.Viewport>
        <Composer
          placeholder={placeholder}
          disabled={composerDisabled}
          showStop={showStop && allowCancel}
        />
      </ThreadPrimitive.Root>
    </AssistantRuntimeProvider>
  );
}

/** Pull plain text out of an assistant-ui composer message. */
export function extractText(message: AppendMessage): string {
  const parts: string[] = [];
  for (const part of message.content) {
    if (part.type === "text" && part.text.trim()) parts.push(part.text.trim());
  }
  return parts.join("\n");
}

function norm(value: unknown): string {
  return String(value ?? "")
    .trim()
    .toLowerCase();
}

export interface SupportViewer {
  id?: string | null;
  name?: string | null;
  email?: string | null;
}

export interface BackendToAuiOptions {
  /** `employee` keeps the legacy user-right/human-left mapping (help page).
   * `support` flips the perspective: the employee is incoming (left) and the
   * current viewer's own human messages plus AI agent responses are outgoing
   * (right). */
  perspective?: "employee" | "support";
  viewer?: SupportViewer | null;
  assignedTo?: string | null;
  assignedToUserId?: string | null;
}

function roleFor(speaker: string): "user" | "assistant" | "system" {
  const s = (speaker ?? "").toLowerCase();
  if (s.includes("system")) return "system";
  if (
    s === "user" ||
    s === "employee" ||
    s.includes("employee") ||
    s === "human-user"
  )
    return "user";
  return "assistant";
}

/** True when a `human`-speaker backend message was written by the signed-in
 * support viewer. Viewer identity prefers the stable id
 * (`assigned_to_user_id` / `user.id` and any author id on the message) and
 * falls back to display-name/email comparison against `message.name`. */
export function isOwnHumanMessage(
  message: BackendMessage,
  viewer: SupportViewer | null | undefined,
  assignedTo?: string | null,
  assignedToUserId?: string | null,
): boolean {
  if ((message.speaker ?? "").toLowerCase() !== "human") return false;
  if (!viewer) return false;
  const viewerId = (viewer.id ?? "").trim();
  const viewerName = norm(viewer.name);
  const viewerEmail = norm(viewer.email);
  const msgName = norm(message.name);
  const raw = message as unknown as Record<string, unknown>;
  const authorIds = [raw.author_id, raw.authorId, raw.user_id, raw.userId]
    .map((v) => String(v ?? "").trim())
    .filter(Boolean);
  if (viewerId && authorIds.includes(viewerId)) return true;

  // Viewer is the thread owner (prefer stable id, else display name).
  const ownerById =
    !!viewerId && !!assignedToUserId && viewerId === assignedToUserId;
  const ownerByName =
    !assignedToUserId &&
    !!viewerName &&
    !!assignedTo &&
    viewerName === norm(assignedTo);
  const isOwner = ownerById || ownerByName;

  if (msgName) {
    if (viewerName && msgName === viewerName) return true;
    if (viewerEmail && msgName === viewerEmail) return true;
    // Nameless-owner fallback below; a concrete different name is someone else.
    if (isOwner && assignedTo && msgName === norm(assignedTo)) return true;
    return false;
  }
  // Legacy rows may carry no name: attribute to the owner, nobody else.
  return isOwner;
}

/** Map backend thread messages to assistant-ui ThreadMessageLike entries.
 *
 * Stable ids (`m-{seq}`) are never changed. Message text is never mutated:
 * sender names live in `metadata.custom.sender` and render as a small label
 * above incoming bubbles, never as a `Name: text` prefix. The viewer's own
 * human messages carry no sender label.
 *
 * Perspectives:
 * - `employee` (default): user/employee right, human/agent left.
 * - `support`: employee (`user`) left/incoming; the viewer's own `human`
 *   messages plus AI `agent` responses right/outgoing; other humans stay
 *   incoming. */
export function backendToAui(
  messages: BackendMessage[],
  opts?: BackendToAuiOptions,
): ThreadMessageLike[] {
  const perspective = opts?.perspective ?? "employee";
  if (perspective !== "support") {
    return messages.map((m) => {
      const role = roleFor(m.speaker);
      const sender =
        role === "assistant" && m.name ? String(m.name) : undefined;
      return {
        id: `m-${m.seq}`,
        role,
        content: [{ type: "text", text: m.text }],
        createdAt: m.at ? new Date(m.at) : undefined,
        ...(sender
          ? { metadata: { custom: { sender, speaker: m.speaker } } }
          : undefined),
      } satisfies ThreadMessageLike;
    });
  }

  const viewer = opts?.viewer ?? null;
  const assignedTo = opts?.assignedTo ?? null;
  const assignedToUserId = opts?.assignedToUserId ?? null;
  return messages.map((m) => {
    const s = (m.speaker ?? "").toLowerCase();
    const createdAt = m.at ? new Date(m.at) : undefined;
    if (s.includes("system")) {
      return {
        id: `m-${m.seq}`,
        role: "system",
        content: [{ type: "text", text: m.text }],
        createdAt,
      } satisfies ThreadMessageLike;
    }
    if (
      s === "user" ||
      s === "employee" ||
      s.includes("employee") ||
      s === "human-user"
    ) {
      // Employee is the sender/incoming for a support viewer.
      const sender = m.name ? String(m.name) : undefined;
      return {
        id: `m-${m.seq}`,
        role: "assistant",
        content: [{ type: "text", text: m.text }],
        createdAt,
        ...(sender
          ? { metadata: { custom: { sender, speaker: m.speaker } } }
          : undefined),
      } satisfies ThreadMessageLike;
    }
    if (s === "human") {
      if (isOwnHumanMessage(m, viewer, assignedTo, assignedToUserId)) {
        // Own outgoing message: no name prefix, no sender label.
        return {
          id: `m-${m.seq}`,
          role: "user",
          content: [{ type: "text", text: m.text }],
          createdAt,
        } satisfies ThreadMessageLike;
      }
      const sender = m.name ? String(m.name) : "Support";
      return {
        id: `m-${m.seq}`,
        role: "assistant",
        content: [{ type: "text", text: m.text }],
        createdAt,
        metadata: { custom: { sender, speaker: m.speaker } },
      } satisfies ThreadMessageLike;
    }
    // AI agent responses read as sent by support in this perspective:
    // outgoing, just like the viewer's own human replies.
    if (s === "agent") {
      return {
        id: `m-${m.seq}`,
        role: "user",
        content: [{ type: "text", text: m.text }],
        createdAt,
      } satisfies ThreadMessageLike;
    }
    // Anything else: incoming, labelled when named.
    const sender = m.name ? String(m.name) : undefined;
    return {
      id: `m-${m.seq}`,
      role: "assistant",
      content: [{ type: "text", text: m.text }],
      createdAt,
      ...(sender
        ? { metadata: { custom: { sender, speaker: m.speaker } } }
        : undefined),
    } satisfies ThreadMessageLike;
  });
}
