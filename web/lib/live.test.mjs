// node --test: the live-updates stream never leaves a view quietly stale.
import assert from "node:assert/strict";
import { test } from "node:test";

import { RESYNC, RETRY_MS, createLive } from "./client/live.ts";

function fakeBrowser() {
  const sources = [];
  const timers = [];
  return {
    sources,
    timers,
    open(url) {
      const handlers = {};
      const source = {
        url,
        readyState: 0,
        closed: false,
        close() {
          this.closed = true;
          this.readyState = 2;
        },
        addEventListener(type, fn) {
          (handlers[type] ??= []).push(fn);
        },
        fire(type, event = {}) {
          for (const fn of handlers[type] ?? []) fn(event);
        },
      };
      sources.push(source);
      return source;
    },
    later(run, ms) {
      const timer = { run, ms, canceled: false };
      timers.push(timer);
      return timer;
    },
    cancel(timer) {
      timer.canceled = true;
    },
  };
}

function listening(live) {
  const seen = [];
  const stop = live.subscribe((event) => seen.push(event.type));
  return { seen, stop };
}

test("events reach the listeners; the first open is no gap", () => {
  const browser = fakeBrowser();
  const live = createLive(browser);
  const { seen } = listening(live);
  const [source] = browser.sources;
  source.fire("open");
  source.fire("message", { data: JSON.stringify({ type: "message", thread_id: "t1" }) });
  source.fire("task", { data: "not json" });
  assert.deepEqual(seen, ["message"]);
});

test("after it reconnects by itself, every listener fetches again", () => {
  const browser = fakeBrowser();
  const live = createLive(browser);
  const { seen } = listening(live);
  const [source] = browser.sources;
  source.fire("open");
  source.readyState = 0; // EventSource reconnecting
  source.fire("error");
  assert.equal(browser.sources.length, 1); // left to reconnect by itself
  source.fire("open");
  assert.deepEqual(seen, [RESYNC]);
});

test("a stream that gave up is opened again, waiting longer each time, then resyncs", () => {
  const browser = fakeBrowser();
  const live = createLive(browser);
  const { seen } = listening(live);
  let [source] = browser.sources;
  source.fire("open");
  for (const wait of RETRY_MS.concat(RETRY_MS.at(-1))) {
    source.readyState = 2; // closed for good: an error answer
    source.fire("error");
    const timer = browser.timers.at(-1);
    assert.equal(timer.ms, wait);
    timer.run();
    source = browser.sources.at(-1);
  }
  assert.equal(browser.sources.length, RETRY_MS.length + 2);
  source.fire("open");
  assert.deepEqual(seen, [RESYNC]);
  // Open again: the wait starts over.
  source.readyState = 2;
  source.fire("error");
  assert.equal(browser.timers.at(-1).ms, RETRY_MS[0]);
});

test("nobody listening: nothing stays open, nothing retries", () => {
  const browser = fakeBrowser();
  const live = createLive(browser);
  const { stop } = listening(live);
  const [source] = browser.sources;
  source.readyState = 2;
  source.fire("error");
  const [timer] = browser.timers;
  stop();
  assert.equal(timer.canceled, true);
  assert.equal(browser.sources.length, 1);
  live.resync(); // no listener: nothing to tell
  assert.equal(browser.sources.length, 1);
});

test("one stream for every listener in the window", () => {
  const browser = fakeBrowser();
  const live = createLive(browser);
  const one = listening(live);
  const two = listening(live);
  assert.equal(browser.sources.length, 1);
  one.stop();
  assert.equal(browser.sources[0].closed, false);
  two.stop();
  assert.equal(browser.sources[0].closed, true);
});

test("a page back from the back-forward cache starts a new stream and fetches again", () => {
  const browser = fakeBrowser();
  const live = createLive(browser);
  const { seen } = listening(live);
  const [first] = browser.sources;
  first.fire("open");
  live.reopen();
  assert.equal(first.closed, true);
  assert.equal(browser.sources.length, 2);
  assert.deepEqual(seen, [RESYNC]);
  first.fire("open"); // the old one says nothing any more
  assert.deepEqual(seen, [RESYNC]);
  live.resync(); // the window shown again
  assert.deepEqual(seen, [RESYNC, RESYNC]);
});
