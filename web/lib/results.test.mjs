// node --test: a finished task's result sits in the conversation where it finished.
import assert from "node:assert/strict";
import { test } from "node:test";

import { resultsAfter } from "./results.ts";

const at = (minute) => `2026-11-22T10:${String(minute).padStart(2, "0")}:00Z`;
const messages = [
  { id: "asked", created_at: at(0) },
  { id: "working", created_at: at(0) },
  { id: "follow-up", created_at: at(5) },
  { id: "working-again", created_at: at(5) },
];

test("a result follows the reply that started it, and comes before a later follow-up", () => {
  const first = { id: "a", created_at: at(0), finished_at: at(2) };
  const second = { id: "b", created_at: at(5), finished_at: at(7) };
  const after = resultsAfter(messages, [first, second]);
  assert.deepEqual(after.get("working"), [first]);
  assert.deepEqual(after.get("working-again"), [second]);
  assert.equal(after.get("follow-up"), undefined);
});

test("two results that finished in the same gap stay in the order given", () => {
  const one = { id: "a", created_at: at(0), finished_at: at(3) };
  const two = { id: "b", created_at: at(0), finished_at: at(4) };
  assert.deepEqual(resultsAfter(messages, [one, two]).get("working"), [one, two]);
});

test("a result with no finish time is placed by when it started", () => {
  const task = { id: "a", created_at: at(6), finished_at: null };
  assert.deepEqual(resultsAfter(messages, [task]).get("working-again"), [task]);
});

test("one that finished before every message follows the first, and no messages means nowhere", () => {
  const early = { id: "a", created_at: at(0), finished_at: "2026-11-22T09:00:00Z" };
  assert.deepEqual(resultsAfter(messages, [early]).get("asked"), [early]);
  assert.equal(resultsAfter([], [early]).size, 0);
});
