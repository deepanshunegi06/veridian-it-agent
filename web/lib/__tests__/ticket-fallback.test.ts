/* Regression test: employee ticket fallback never shows "unavailable" when a
 * ticket number is already known elsewhere.
 *
 * Covers the reported /help failure where TicketDetails derived only from
 * GET /threads/{id} while the list summary already carried ticket_id:
 * fallback order is detail -> selected list summary -> known live ticket map
 * -> in-flight live ticket -> null (genuinely unavailable). The ticket alias
 * (`ticket` alongside `ticket_id`) and raise_ticket actions are honored, and
 * a known ticket stays visible even when the detail refresh races (null).
 *
 * Run: `npm run test:unit` (compiles lib/__tests__ + lib/api.ts + lib/auth.ts
 * with tsc, then runs the output with node --test). No extra dependencies.
 */
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { resolveEmployeeTicketId, threadTicketId } from "../api";

describe("threadTicketId", () => {
  it("prefers ticket_id, then ticket alias, then actions", () => {
    assert.equal(threadTicketId({ ticket_id: "TK-1" }), "TK-1");
    assert.equal(
      threadTicketId({ ticket_id: null, ticket: "TK-2" } as never),
      "TK-2",
    );
    assert.equal(
      threadTicketId({
        actions: [{ tool: "raise_ticket", ticket: "TK-3" } as never],
      }),
      "TK-3",
    );
    assert.equal(threadTicketId(null), null);
    assert.equal(threadTicketId({} as never), null);
  });
});

describe("resolveEmployeeTicketId", () => {
  it("uses detail first", () => {
    assert.equal(
      resolveEmployeeTicketId({
        detail: { ticket_id: "TK-D" },
        summary: { ticket_id: "TK-S" },
        ticketMap: { "LIVE-1": "TK-M" },
        selectedId: "LIVE-1",
        liveTicketId: "TK-L",
      }),
      "TK-D",
    );
  });

  it("falls back to the selected list summary when detail races", () => {
    assert.equal(
      resolveEmployeeTicketId({
        detail: { ticket_id: null, actions: [] },
        summary: { ticket_id: "TK-S" },
        ticketMap: {},
        selectedId: "LIVE-1",
        liveTicketId: null,
      }),
      "TK-S",
    );
  });

  it("falls back to the known live ticket map, then the in-flight ticket", () => {
    assert.equal(
      resolveEmployeeTicketId({
        detail: null,
        summary: null,
        ticketMap: { "LIVE-1": "TK-M" },
        selectedId: "LIVE-1",
        liveTicketId: "TK-L",
      }),
      "TK-M",
    );
    assert.equal(
      resolveEmployeeTicketId({
        detail: { ticket_id: null },
        summary: { ticket_id: null },
        ticketMap: {},
        selectedId: "LIVE-1",
        liveTicketId: "TK-L",
      }),
      "TK-L",
    );
  });

  it("returns null only when nothing is known", () => {
    assert.equal(
      resolveEmployeeTicketId({
        detail: { ticket_id: null, actions: [] },
        summary: { ticket_id: null },
        ticketMap: {},
        selectedId: "LIVE-1",
        liveTicketId: null,
      }),
      null,
    );
    assert.equal(
      resolveEmployeeTicketId({
        detail: null,
        summary: null,
        ticketMap: { "LIVE-9": "TK-X" },
        selectedId: "LIVE-1",
        liveTicketId: null,
      }),
      null,
    );
  });
});
