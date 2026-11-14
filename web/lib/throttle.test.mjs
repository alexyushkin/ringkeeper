// node --test: a burst of live events reloads a little, and always once after the last.
import assert from "node:assert/strict";
import { test } from "node:test";

import { throttle } from "./throttle.ts";

test("the first call runs at once; a burst runs once more at the end", () => {
  const timers = [];
  let runs = 0;
  const call = throttle(() => (runs += 1), 1500, (fn, ms) => timers.push({ fn, ms }));
  call();
  assert.equal(runs, 1);
  call();
  call();
  call();
  assert.equal(runs, 1);
  assert.equal(timers.length, 1); // one trailing run for the whole burst
  assert.ok(timers[0].ms > 0 && timers[0].ms <= 1500);
  timers[0].fn();
  assert.equal(runs, 2);
});
