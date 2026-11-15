// node --test: every screen that shows background work fetches it again when that work
// finishes, with no reload (lib/client/refresh.ts, used by useApi and usePaged).
import assert from "node:assert/strict";
import { test } from "node:test";

import { LIVE_TYPES, RESYNC } from "./client/live.ts";
import { WATCHES, changes, watchOf } from "./client/refresh.ts";

const finished = {
  task: { type: "task", task_id: "t1", status: "succeeded" },
  approval: { type: "approval", decision_id: "d1", status: "approved" },
  message: { type: "message", thread_id: "th1" },
  routed: { type: "message_routed", thread_id: "th1" },
  backup: { type: "job", kind: "backup", status: "done" },
  upload: { type: "job", kind: "backup_upload", status: "done" },
  uploading: { type: "job", kind: "backup_upload", status: "started" },
  health: { type: "job", kind: "health", status: "done" },
};
const unrelated = { type: "session_event", task_id: "t1" }; // a step in a task's transcript

// Each screen: a read it really makes, and the finished work it must show.
const SCREENS = [
  ["Home: recent conversations", "threads", [finished.message, finished.routed, finished.task, finished.approval]],
  ["Home: first steps, and the backup notices", "start", [finished.task, finished.routed, finished.backup, finished.upload]],
  ["Home: what happened yesterday", "digest", [finished.task, finished.approval]],
  ["Home: sent for later", "scheduled", [finished.message, finished.routed]],
  ["Home: interrupted tasks", "tasks/interrupted", [finished.task]],
  ["Home and a project: what's being watched", "watching", [finished.task]],
  ["Tasks, and a client's or project's tasks", "tasks", [finished.task]],
  ["Tasks: old failed ones to archive", "cleanup/old-tasks", [finished.task]],
  ["Approvals, and the count beside it in the menu", "decisions", [finished.approval]],
  ["Approvals: trusted for an hour", "trusts", [finished.approval]],
  ["A task's changes", "tasks/3f2a/changes", [finished.approval, finished.task]],
  ["A conversation's changes", "threads/9b1c/changes", [finished.approval, finished.task]],
  ["A pass's changes", "passes/acme-week/changes", [finished.approval, finished.task]],
  ["A routine's runs", "routines/42/runs", [finished.task]],
  ["Settings: Backup, and Google Drive", "desktop/backup", [finished.backup, finished.upload, finished.uploading]],
  ["Connections: whether each one works", "connections", [finished.health]],
];

for (const [page, read, events] of SCREENS) {
  test(`${page} shows finished work without a reload`, () => {
    assert.equal(watchOf(read)?.page, page, `${read} is in the table as this screen`);
    for (const event of events) assert.equal(changes(read, event), true, `${read} on ${event.type} ${event.kind ?? ""}`);
    assert.equal(changes(read, { type: RESYNC }), true, "and after events may have been missed");
    assert.equal(changes(read, unrelated), false, "a task's transcript line alone doesn't fetch it");
  });
}

test("every row of the table is one of the screens tested above", () => {
  assert.deepEqual(
    WATCHES.map((w) => w.page).sort(),
    SCREENS.map(([page]) => page).sort(),
  );
});

test("a backup or upload doesn't fetch screens it doesn't change", () => {
  for (const read of ["threads", "tasks", "decisions", "connections"]) {
    assert.equal(changes(read, finished.backup), false, read);
    assert.equal(changes(read, finished.upload), false, read);
  }
  assert.equal(changes("desktop/backup", finished.task), false);
});

test("a read not in the table isn't fetched again by itself", () => {
  assert.equal(watchOf("settings"), undefined);
  assert.equal(watchOf("tasks/3f2a"), undefined); // a task's own page listens by its id
  assert.equal(changes("settings", { type: RESYNC }), false);
  assert.equal(changes(null, finished.task), false);
});

test("the event stream carries background jobs", () => {
  assert.ok(LIVE_TYPES.includes("job"));
  assert.equal(RESYNC, "resync"); // refresh.ts keeps its own copy of the name
});
