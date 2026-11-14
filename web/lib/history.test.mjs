// node --test: Back and Forward follow the app's own history, put each page back as it was,
// and close a dialog before leaving a page.
import assert from "node:assert/strict";
import { test } from "node:test";

import { createHistory } from "./client/history.ts";

/** A browser's history, enough of it: entries, back, forward, popstate, scroll, storage. */
function fakeWindow({ stored = {}, height = 5000 } = {}) {
  const entries = [{ state: { __NA: true }, url: "/" }];
  let at = 0;
  const handlers = [];
  const timers = [];
  const win = {
    scrollY: 0,
    height,
    timers,
    entries,
    get at() {
      return at;
    },
    sessionStorage: {
      data: { ...stored },
      getItem(key) {
        return this.data[key] ?? null;
      },
      setItem(key, value) {
        this.data[key] = value;
      },
    },
    history: {
      scrollRestoration: "auto",
      get state() {
        return entries[at].state;
      },
      pushState(state, _unused, url) {
        entries.splice(at + 1);
        entries.push({ state, url });
        at += 1;
      },
      replaceState(state, _unused, url) {
        entries[at] = { state, url: url ?? entries[at].url };
      },
      back() {
        if (at === 0) return;
        at -= 1;
        for (const h of handlers) h({ state: entries[at].state });
      },
      forward() {
        if (at === entries.length - 1) return;
        at += 1;
        for (const h of handlers) h({ state: entries[at].state });
      },
    },
    input: [],
    hashHandlers: [],
    addEventListener(type, handler) {
      if (type === "popstate") handlers.push(handler);
      else if (type === "hashchange") win.hashHandlers.push(handler);
      else win.input.push(handler);
    },
    /** A link to #somewhere: a new entry, then hashchange only (no popstate). */
    followFragment(url) {
      entries.splice(at + 1);
      entries.push({ state: null, url });
      at += 1;
      for (const h of win.hashHandlers) h({});
    },
    scrollTo(_x, y) {
      win.scrollY = Math.min(y, win.height);
    },
    scrollable() {
      return win.height;
    },
    later(run, ms) {
      timers.push({ run, ms });
    },
  };
  return win;
}

test("nowhere to go at first; each page and tab is a step; Back and Forward follow them", () => {
  const win = fakeWindow();
  const history = createHistory(win);
  assert.deepEqual(history.place(), { index: 0, furthest: 0 }); // both arrows greyed out
  win.history.pushState({ __NA: true }, "", "/thread?id=t1"); // a conversation
  win.history.pushState({ __NA: true }, "", "/task?id=k1"); // its task
  win.history.pushState({ __NA: true }, "", "/settings#appearance"); // a page, then a tab
  win.history.pushState({ __NA: true }, "", "/settings#advanced");
  assert.deepEqual(history.place(), { index: 4, furthest: 4 }); // Back, no Forward
  history.back();
  assert.equal(win.entries[win.at].url, "/settings#appearance");
  history.back();
  history.back();
  assert.equal(win.entries[win.at].url, "/thread?id=t1");
  assert.deepEqual(history.place(), { index: 1, furthest: 4 }); // both arrows
  history.forward();
  assert.equal(win.entries[win.at].url, "/task?id=k1");
  // Somewhere new from here: what was ahead is gone.
  win.history.pushState({ __NA: true }, "", "/approvals");
  assert.deepEqual(history.place(), { index: 3, furthest: 3 });
  history.forward(); // nowhere to go: nothing happens
  assert.equal(win.entries[win.at].url, "/approvals");
  assert.equal(win.entries[win.at].state.__NA, true); // Next's own state is kept
});

test("a link to a place on the page is a step too", () => {
  const win = fakeWindow();
  const history = createHistory(win);
  win.history.pushState({}, "", "/settings");
  // The browser's own: a new entry with no state, then popstate.
  win.entries.push({ state: null, url: "/settings#claude" });
  win.history.forward();
  assert.deepEqual(history.place(), { index: 2, furthest: 2 });
  history.back();
  assert.equal(win.entries[win.at].url, "/settings");
});

test("a link to #somewhere that only says hashchange is a step, and Back returns to it", () => {
  const win = fakeWindow();
  const history = createHistory(win);
  win.history.pushState({ __NA: true }, "", "/settings");
  win.followFragment("/settings#claude");
  assert.deepEqual(history.place(), { index: 2, furthest: 2 });
  win.scrollY = 575;
  win.history.pushState({}, "", "/tasks");
  win.scrollY = 0;
  history.back();
  assert.equal(win.entries[win.at].url, "/settings#claude");
  assert.equal(win.scrollY, 575);
  assert.deepEqual(history.place(), { index: 2, furthest: 3 });
  // It carries the page's own state (Next's), so Back to it doesn't reload the page.
  assert.equal(win.entries[win.at].state.__NA, true);
});

test("Back at the app's first page doesn't leave the app", () => {
  const win = fakeWindow();
  const history = createHistory(win);
  history.back();
  assert.equal(win.at, 0);
});

test("going back puts the scroll where it was, once the page is tall enough again", () => {
  const win = fakeWindow();
  const history = createHistory(win);
  win.history.pushState({}, "", "/thread?id=t1");
  win.scrollY = 1800; // read down the conversation
  win.history.pushState({}, "", "/task?id=k1");
  win.scrollY = 0;
  win.height = 600; // the conversation's data hasn't loaded yet
  history.back();
  assert.equal(win.scrollY, 600); // as far as it can go for now
  win.height = 5000; // loaded
  win.timers.at(-1).run();
  assert.equal(win.scrollY, 1800);
  win.scrollY = 150; // the page scrolls itself to a section its address names
  win.timers.at(-1).run();
  assert.equal(win.scrollY, 1800); // held where it was
  history.forward();
  assert.equal(win.scrollY, 0); // the task page, as it was
});

test("scrolling yourself while it's put back wins", () => {
  const win = fakeWindow();
  const history = createHistory(win);
  win.history.pushState({}, "", "/a");
  win.scrollY = 900;
  win.history.pushState({}, "", "/b");
  history.back();
  assert.equal(win.scrollY, 900);
  for (const h of win.input) h(); // a wheel, a key, a tap
  win.scrollY = 40;
  win.timers.at(-1).run();
  assert.equal(win.scrollY, 40);
});

test("which details were open is kept per page in history", () => {
  const win = fakeWindow();
  const history = createHistory(win);
  win.history.pushState({}, "", "/task?id=k1");
  assert.equal(history.wasOpen("how it was split"), null);
  history.setOpen("how it was split", true);
  win.history.pushState({}, "", "/task?id=k2");
  assert.equal(history.wasOpen("how it was split"), null); // another page
  history.back();
  assert.equal(history.wasOpen("how it was split"), true);
});

test("with a dialog open, Back closes it and the page stays", () => {
  const win = fakeWindow();
  const history = createHistory(win);
  win.history.pushState({}, "", "/connections");
  let closed = 0;
  const release = history.holdBack(() => (closed += 1));
  history.forward(); // nothing ahead, and a dialog is open: nothing happens
  history.back();
  assert.equal(closed, 1);
  assert.equal(win.entries[win.at].url, "/connections"); // still here
  history.back(); // the dialog is closed now: Back goes back
  assert.equal(win.at, 0);
  release(); // closing it itself, later, changes nothing
  assert.equal(closed, 1);
});

test("a reload keeps where it is and what's ahead", () => {
  const first = fakeWindow();
  const history = createHistory(first);
  first.history.pushState({}, "", "/a");
  first.history.pushState({}, "", "/b");
  history.back();
  // The same tab, reloaded on /a: history.state and the session's storage survive.
  const again = fakeWindow({ stored: first.sessionStorage.data });
  again.entries[0] = { state: first.entries[first.at].state, url: "/a" };
  const reloaded = createHistory(again);
  assert.deepEqual(reloaded.place(), { index: 1, furthest: 2 });
});
