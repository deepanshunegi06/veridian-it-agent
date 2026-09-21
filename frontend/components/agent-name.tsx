"use client";

import { useState } from "react";

export function getStoredName(key: string): string {
  try {
    return localStorage.getItem(key) ?? "";
  } catch {
    return "";
  }
}

export function storeName(key: string, name: string): void {
  try {
    localStorage.setItem(key, name);
  } catch {
    /* storage unavailable — name just won't persist */
  }
}

/** Modal asking for an agent display name (min 1 char), remembered. */
export function NameModal({
  title,
  initial,
  busy,
  onSubmit,
  onClose,
}: {
  title: string;
  initial: string;
  busy: boolean;
  onSubmit: (name: string) => void;
  onClose: () => void;
}) {
  // Parents remount via `key` when `initial` should reset.
  const [value, setValue] = useState(initial);
  const valid = value.trim().length >= 1;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={title}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={() => {
        if (!busy) onClose();
      }}
    >
      <div
        className="w-full max-w-sm rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-950"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-sm font-semibold">{title}</h2>
        <p className="mt-1 text-xs text-zinc-500">
          Your name is shown in the thread and remembered on this device.
        </p>
        <input
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && valid && !busy) onSubmit(value.trim());
            if (e.key === "Escape" && !busy) onClose();
          }}
          placeholder="e.g. Priya"
          aria-label="Your name"
          autoFocus
          className="mt-3 w-full rounded-md border border-zinc-300 px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900"
        />
        <div className="mt-3 flex justify-end gap-2">
          <button
            onClick={onClose}
            disabled={busy}
            className="rounded-md border border-zinc-300 px-3 py-1.5 text-sm hover:bg-zinc-100 disabled:opacity-50 dark:border-zinc-700 dark:hover:bg-zinc-800"
          >
            Cancel
          </button>
          <button
            onClick={() => onSubmit(value.trim())}
            disabled={!valid || busy}
            className="rounded-md bg-zinc-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-zinc-700 disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
          >
            {busy ? "…" : "Continue"}
          </button>
        </div>
      </div>
    </div>
  );
}
