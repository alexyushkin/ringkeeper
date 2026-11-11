// node --test: the line a conversation shows while the router works out where a request goes.
import assert from "node:assert/strict";
import { test } from "node:test";

import { FINDING, STILL, routingLine } from "./routing-wait.ts";

const sent = "2026-11-11T10:00:00Z";
const at = (seconds) => new Date(sent).getTime() + seconds * 1000;

test("it says it's finding the project, then after 20 seconds that it's still working it out", () => {
  assert.deepEqual(routingLine(sent, at(0)), { text: FINDING, changesIn: 20_000 });
  assert.deepEqual(routingLine(sent, at(15)), { text: FINDING, changesIn: 5_000 });
  assert.deepEqual(routingLine(sent, at(20)), { text: STILL, changesIn: null });
  assert.deepEqual(routingLine(sent, at(400)), { text: STILL, changesIn: null });
});

test("the words", () => {
  assert.equal(FINDING, "Finding the right project…");
  assert.equal(STILL, "Still working out where this goes…");
});
