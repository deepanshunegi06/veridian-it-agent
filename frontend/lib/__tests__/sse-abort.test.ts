/* Regression test: live SSE teardown must never surface as a console error.
 *
 * Covers the reported IT-thread failure (`AbortError: signal is aborted
 * without reason` when opening a chat): aborting the subscription signal
 * mid-stream (route change, ticket switch, strict-mode remount) resolves
 * silently, cancels + releases the reader, keeps events already received,
 * and never produces an unhandled rejection. Real network/HTTP errors and
 * pre-aborted signals keep their contract: errors still reject, and an
 * already-aborted signal never touches the network.
 *
 * Run: `npm run test:unit` (compiles this + lib/api.ts + lib/auth.ts with
 * tsc, then runs the output with node --test). No extra dependencies.
 */
import { afterEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import { isAbortError, streamGetSSE } from "../api";

const realFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = realFetch;
});

const unexpected: unknown[] = [];
process.on("unhandledRejection", (e) => {
  unexpected.push(e);
});

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

interface Probe {
  fetchCalls: number;
  readPending: boolean;
  released: boolean;
  cancelled: boolean;
}

function newProbe(): Probe {
  return { fetchCalls: 0, readPending: false, released: false, cancelled: false };
}

/** Scripted SSE fetch: emits `frames` first, then pends like a live stream.
 * Mirrors undici: aborting the signal rejects the pending read with the
 * abort reason (`signal is aborted without reason` by default). */
function sseFetch(probe: Probe, frames: string[]): typeof fetch {
  const mock = async (_url: string | URL | Request, init?: RequestInit) => {
    probe.fetchCalls += 1;
    const signal = init?.signal ?? null;
    let step = 0;
    let rejectPending: ((e: unknown) => void) | null = null;
    const body = {
      getReader() {
        return {
          read(): Promise<ReadableStreamReadResult<Uint8Array>> {
            if (step < frames.length) {
              const chunk = new TextEncoder().encode(frames[step]);
              step += 1;
              return Promise.resolve({ done: false, value: chunk });
            }
            probe.readPending = true;
            return new Promise<ReadableStreamReadResult<Uint8Array>>(
              (_resolve, reject) => {
                rejectPending = reject;
              },
            );
          },
          releaseLock() {
            probe.released = true;
          },
          cancel() {
            probe.cancelled = true;
            return Promise.resolve();
          },
        };
      },
    };
    signal?.addEventListener("abort", () => {
      if (rejectPending) rejectPending(signal.reason);
    });
    return { ok: true, body } as Response;
  };
  return mock as unknown as typeof fetch;
}

async function waitFor(cond: () => boolean, what: string): Promise<void> {
  const deadline = Date.now() + 2000;
  while (!cond()) {
    if (Date.now() > deadline) throw new Error(`timed out waiting for ${what}`);
    await sleep(5);
  }
}

describe("sse abort handling", () => {
  it("classifies expected aborts without swallowing real errors", () => {
    // Exact shape from the reported console error.
    assert.equal(
      isAbortError(new DOMException("signal is aborted without reason", "AbortError")),
      true,
    );
    const ctrl = new AbortController();
    ctrl.abort();
    assert.equal(isAbortError(ctrl.signal.reason), true);
    assert.equal(isAbortError({ code: 20 }), true);
    assert.equal(isAbortError(new TypeError("fetch failed")), false);
    assert.equal(isAbortError(new Error("boom")), false);
    assert.equal(isAbortError(null), false);
    assert.equal(isAbortError("signal is aborted without reason"), false);
  });

  it("abort mid-stream resolves silently and releases the reader", async () => {
    const probe = newProbe();
    globalThis.fetch = sseFetch(probe, [
      'event: message\ndata: {"seq":1,"speaker":"human","text":"hi"}\n\n',
    ]);
    const ctrl = new AbortController();
    const kinds: string[] = [];
    const done = streamGetSSE(
      "/threads/req-1/stream",
      (ev) => {
        kinds.push(ev.kind);
      },
      ctrl.signal,
    );
    await waitFor(() => probe.readPending, "live read to pend");
    await sleep(10); // let the scripted frame dispatch first
    ctrl.abort(); // cleanup line from the report
    await Promise.race([
      done,
      sleep(2000).then(() => {
        throw new Error("subscription did not settle after abort");
      }),
    ]);
    await sleep(25); // flush any late unhandled rejections
    assert.deepEqual(kinds, ["message"]); // live updates kept working
    assert.equal(probe.released, true); // no leaked reader/stream
    assert.equal(probe.cancelled, true); // pending read settled promptly
    assert.deepEqual(unexpected, []);
  });

  it("real network errors still reject", async () => {
    globalThis.fetch = (() =>
      Promise.reject(new TypeError("fetch failed"))) as unknown as typeof fetch;
    await assert.rejects(
      streamGetSSE("/threads/req-1/stream", () => {}),
      /fetch failed/,
    );
  });

  it("HTTP errors still reject with status", async () => {
    globalThis.fetch = (async () =>
      ({
        ok: false,
        status: 503,
        text: () => Promise.resolve("unavailable"),
      }) as Response) as unknown as typeof fetch;
    await assert.rejects(streamGetSSE("/threads/req-1/stream", () => {}), /503/);
  });

  it("already-aborted signal never touches the network", async () => {
    const probe = newProbe();
    globalThis.fetch = sseFetch(probe, []);
    const ctrl = new AbortController();
    ctrl.abort();
    await streamGetSSE("/threads/req-1/stream", () => {}, ctrl.signal);
    assert.equal(probe.fetchCalls, 0);
  });
});
