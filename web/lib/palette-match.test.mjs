// node --test: what the Ctrl+K search finds, and where a Settings section opens.
import assert from "node:assert/strict";
import { test } from "node:test";

import { score, wordsHit } from "./palette-match.ts";
import { SETTINGS_SECTIONS, namedInHash, sectionHref, shownSections } from "./settings-sections.ts";

/** What a search lists first, among the Settings sections this install shows. */
function best(query, desktop = true) {
  return shownSections(desktop)
    .map((s) => ({ s, n: score(query, s.title, s.tab, `settings ${s.words}`) }))
    .filter((m) => m.n !== null)
    .sort((a, b) => b.n - a.n)
    .map((m) => m.s.title);
}

test("\"my setup\" finds Check my setup: it found nothing before", () => {
  assert.equal(best("my setup")[0], "Check my setup");
  assert.equal(best("setup")[0], "Check my setup");
});

test("plain words land: test, memory, backup, spending cap, send", () => {
  assert.ok(best("test").slice(0, 3).includes("Self-test"));
  assert.ok(best("test").includes("Check my setup"));
  assert.ok(best("memory").includes("Notion for every project"));
  assert.ok(best("backup").slice(0, 3).includes("Automatic backups"));
  assert.equal(best("spending cap")[0], "Spending cap per task");
  assert.equal(best("budget")[0], "Spending cap per task");
  assert.equal(best("send with")[0], "Send with");
  assert.ok(best("telegram").includes("Telegram"));
  assert.ok(best("discord").includes("Discord"));
});

test("a label that matches comes before one found by its words", () => {
  const byLabel = score("backup", "Make a backup", "backup", "settings save copy");
  const byWords = score("backup", "Recovery key", "advanced", "settings security vault backup");
  assert.ok(byLabel > byWords);
  assert.equal(score("backup", "Appearance", "appearance", "settings theme dark"), null);
});

test("a search for a section lands on it, and \"run\" on the action that runs it", () => {
  const section = score("my setup", "Check my setup", "Advanced", "settings setup test");
  const action = score("my setup", "Run Check my setup", "Read-only requests; asks first", "test check setup");
  assert.ok(section > action);
  const ranSection = score("run check", "Check my setup", "Advanced", "settings setup test");
  const ranAction = score("run check", "Run Check my setup", "Read-only requests; asks first", "test check setup");
  assert.ok(ranAction > (ranSection ?? -Infinity));
});

test("words match whole words or their start, not scattered letters", () => {
  assert.equal(wordsHit("back", "backup restore"), true);
  assert.equal(wordsHit("bkp", "backup restore"), false);
  assert.equal(wordsHit("spending cap", "budget cost spending cap"), true);
  assert.equal(wordsHit("", "anything"), false);
});

test("each install lists only the sections it shows", () => {
  const desktop = shownSections(true).map((s) => s.id);
  const server = shownSections(false).map((s) => s.id);
  assert.ok(desktop.includes("self-test") && desktop.includes("backup-auto"));
  assert.ok(!desktop.includes("recovery-key") && !desktop.includes("passkeys"));
  assert.ok(server.includes("recovery-key") && !server.includes("self-test") && !server.includes("backup-auto"));
  assert.ok(server.includes("setup-check") && server.includes("every-project"));
});

test("a section opens at its tab, and Run asks there", () => {
  assert.equal(sectionHref({ tab: "advanced", id: "setup-check" }), "/settings#advanced/setup-check");
  assert.equal(sectionHref({ tab: "advanced", id: "self-test" }, true), "/settings#advanced/self-test/run");
  assert.deepEqual(namedInHash("#advanced/setup-check/run"), { tab: "advanced", section: "setup-check", run: true });
  assert.deepEqual(namedInHash("#memory"), { tab: "memory", section: "", run: false });
});

test("every section names a tab Settings has", () => {
  const tabs = new Set(["claude", "models", "voice", "scans", "developer-apps", "passes", "notifications", "security", "advanced", "memory", "backup", "general", "appearance", "about"]);
  for (const s of SETTINGS_SECTIONS) assert.ok(tabs.has(s.tab), `${s.title}: ${s.tab}`);
});
