// node --test: the line a conversation shows while a request waits for its reply.
import assert from "node:assert/strict";
import { test } from "node:test";

import { NO_REPLY, ROUTING, routingLine } from "./routing-wait.ts";

const sent = "2026-11-11T10:00:00Z";
const at = (seconds) => new Date(sent).getTime() + seconds * 1000;

test("it says Routing at once, and after 10 seconds with no reply says so, with Retry", () => {
  assert.deepEqual(routingLine(sent, at(0)), { text: ROUTING, retry: false, changesIn: 10_000 });
  assert.deepEqual(routingLine(sent, at(7)), { text: ROUTING, retry: false, changesIn: 3_000 });
  assert.deepEqual(routingLine(sent, at(10)), { text: NO_REPLY, retry: true, changesIn: null });
  assert.deepEqual(routingLine(sent, at(400)), { text: NO_REPLY, retry: true, changesIn: null });
  assert.equal(routingLine("not a date", at(0)).retry, true);
});

test("the words", () => {
  assert.equal(ROUTING, "Routing…");
  assert.equal(NO_REPLY, "No reply after 10 seconds.");
});
