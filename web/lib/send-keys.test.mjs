// node --test: what a key does in a text box (send-keys.ts), and that every box uses it.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { DEFAULT_SEND_MODE, isMac, sendChoices, sendHint, sendKey, sendMode } from "./send-keys.ts";

const pc = (mode) => ({ mode, mac: false });
const mac = (mode) => ({ mode, mac: true });

test("Ctrl+Enter mode: Ctrl+Enter sends, Enter and Shift+Enter add a line", () => {
  assert.equal(sendKey({ key: "Enter", ctrlKey: true }, pc("ctrl-enter")), "send");
  assert.equal(sendKey({ key: "Enter" }, pc("ctrl-enter")), "newline");
  assert.equal(sendKey({ key: "Enter", shiftKey: true }, pc("ctrl-enter")), "newline");
  assert.equal(sendKey({ key: "a", ctrlKey: true }, pc("ctrl-enter")), "none");
});

test("Enter mode: Enter sends, Shift+Enter adds a line, Ctrl+Enter still sends", () => {
  assert.equal(sendKey({ key: "Enter" }, pc("enter")), "send");
  assert.equal(sendKey({ key: "Enter", shiftKey: true }, pc("enter")), "newline");
  assert.equal(sendKey({ key: "Enter", ctrlKey: true }, pc("enter")), "send");
  assert.equal(sendKey({ key: "a" }, pc("enter")), "none");
});

test("on a Mac, Cmd+Enter sends; on Windows the Windows key doesn't", () => {
  assert.equal(sendKey({ key: "Enter", metaKey: true }, mac("ctrl-enter")), "send");
  assert.equal(sendKey({ key: "Enter", ctrlKey: true }, mac("ctrl-enter")), "send");
  assert.equal(sendKey({ key: "Enter" }, mac("ctrl-enter")), "newline");
  assert.equal(sendKey({ key: "Enter", metaKey: true }, mac("enter")), "send");
  assert.equal(sendKey({ key: "Enter", shiftKey: true }, mac("enter")), "newline");
  assert.equal(sendKey({ key: "Enter", metaKey: true }, pc("ctrl-enter")), "none");
  assert.equal(sendKey({ key: "Enter", metaKey: true }, pc("enter")), "none");
});

test("Alt+Enter is left alone", () => {
  assert.equal(sendKey({ key: "Enter", altKey: true }, pc("enter")), "none");
  assert.equal(sendKey({ key: "Enter", altKey: true, ctrlKey: true }, pc("ctrl-enter")), "none");
});

test("while an input method is composing a word, Enter never sends", () => {
  for (const mode of ["enter", "ctrl-enter"]) {
    assert.equal(sendKey({ key: "Enter", isComposing: true }, pc(mode)), "none");
    assert.equal(sendKey({ key: "Enter", ctrlKey: true, isComposing: true }, pc(mode)), "none");
    assert.equal(sendKey({ key: "Process", keyCode: 229 }, mac(mode)), "none");
    assert.equal(sendKey({ key: "Enter", keyCode: 229 }, pc(mode)), "none");
    assert.equal(sendKey({ key: "Escape", isComposing: true }, pc(mode)), "none");
  }
});

test("Esc leaves the box, in both modes and with Shift", () => {
  assert.equal(sendKey({ key: "Escape" }, pc("enter")), "blur");
  assert.equal(sendKey({ key: "Escape" }, mac("ctrl-enter")), "blur");
  assert.equal(sendKey({ key: "Escape", shiftKey: true }, pc("ctrl-enter")), "blur");
});

test("nothing is sent while the mention list is open: its own keys work as before", () => {
  for (const mode of ["enter", "ctrl-enter"]) {
    for (const press of [{ key: "Enter" }, { key: "Enter", ctrlKey: true }, { key: "Enter", metaKey: true }, { key: "Escape" }, { key: "Enter", shiftKey: true }]) {
      assert.equal(sendKey(press, { mode, mac: true, mentionOpen: true }), "none");
      assert.equal(sendKey(press, { mode, mac: false, mentionOpen: true }), "none");
    }
  }
  assert.equal(sendKey({ key: "Enter" }, { mode: "enter", mac: false, mentionOpen: false }), "send");
});

test("the hint under a box names the choice, Cmd on a Mac", () => {
  assert.equal(sendHint("ctrl-enter", false), "Ctrl+Enter to send");
  assert.equal(sendHint("ctrl-enter", true), "Cmd+Enter to send");
  assert.equal(sendHint("enter", false), "Enter to send · Shift+Enter for a new line");
  assert.equal(sendHint("enter", true), "Enter to send · Shift+Enter for a new line");
  assert.equal(sendHint("ctrl-enter", false, "ask for changes"), "Ctrl+Enter to ask for changes");
});

test("the setting: Ctrl+Enter by default, anything unknown is the default", () => {
  assert.equal(DEFAULT_SEND_MODE, "ctrl-enter");
  assert.equal(sendMode("enter"), "enter");
  assert.equal(sendMode("ctrl-enter"), "ctrl-enter");
  assert.equal(sendMode(undefined), "ctrl-enter");
  assert.equal(sendMode("shift-enter"), "ctrl-enter");
  assert.deepEqual(sendChoices(false).map(([key]) => key), ["ctrl-enter", "enter"]);
  assert.match(sendChoices(true)[0][1], /^Cmd\+Enter/);
  assert.match(sendChoices(false)[0][1], /^Ctrl\+Enter/);
});

test("a Mac, iPhone or iPad is told apart from Windows and Linux", () => {
  for (const name of ["macOS", "MacIntel", "iPhone", "iPad"]) assert.equal(isMac(name), true, name);
  for (const name of ["Windows", "Win32", "Linux x86_64", "", undefined]) assert.equal(isMac(name), false, String(name));
});

// --- One check per box: it uses the shared hook and shows its hint ---

const source = (path) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");

/** The box's file imports useSendKeys, and the hint is drawn (`keys={...}` on a TextArea draws it). */
function usesSendKeys(path, { keysProp, hint } = {}) {
  const text = source(path);
  assert.match(text, /import \{[^}]*\buseSendKeys\b[^}]*\} from "@\/lib\/client\/use-send-keys"/, `${path} imports useSendKeys`);
  assert.match(text, /useSendKeys\(/, `${path} calls useSendKeys`);
  if (keysProp) assert.match(text, keysProp, `${path} gives the box its keys`);
  if (hint) assert.match(text, hint, `${path} shows the hint`);
  assert.doesNotMatch(text, /event\.key === "Enter" && \(event\.metaKey \|\| event\.ctrlKey\)/, `${path} has no key rule of its own`);
}

test("the TextArea draws the hint and takes the keys", () => {
  const text = source("components/ui.tsx");
  assert.match(text, /onKeyDown=\{keys\?\.onKeyDown\}/);
  assert.match(text, /\{keys \? <p className="text-xs text-muted">\{keys\.hint\}<\/p> : null\}/);
});

test("box: the request box on Home (the Composer)", () => {
  usesSendKeys("components/composer.tsx", { keysProp: /keys\.onKeyDown\(event\)/, hint: /\$\{keys\.hint\}/ });
  assert.match(source("app/(app)/page.tsx"), /<Composer /);
});

test("box: the conversation box", () => {
  assert.match(source("components/views/thread-view.tsx"), /<Composer threadId=/);
  usesSendKeys("components/composer.tsx", { hint: /\$\{keys\.hint\}/ });
});

test("box: the quick request", () => {
  assert.match(source("app/quick/page.tsx"), /<Composer autoFocus/);
  usesSendKeys("components/composer.tsx", { hint: /\$\{keys\.hint\}/ });
});

test("box: Continue this task", () => {
  usesSendKeys("components/views/task-view.tsx", { keysProp: /label="Continue this task"[^\n]*keys=\{resumeKeys\}/ });
});

test("box: answering a question", () => {
  usesSendKeys("components/decision-card.tsx", { keysProp: /label="Your answer"[^\n]*keys=\{answerKeys\}/ });
});

test("box: a plan's note (asks for changes, never approves)", () => {
  const text = source("components/decision-card.tsx");
  usesSendKeys("components/decision-card.tsx", { keysProp: /Sent with your answer, e\.g\. what to change\."[^\n]*keys=\{planKeys\}/ });
  assert.match(text, /answer\("deny", text\);\s*\},\s*\{ verb: "ask for changes" \}/);
});

test("box: Help's question", () => {
  usesSendKeys("components/help-answer.tsx", { keysProp: /<TextArea[\s\S]*?label=\{turns\.length \? "Ask a follow-up"[\s\S]*?keys=\{keys\}/ });
});

test("Settings > General > Send with saves the choice and tells open boxes", () => {
  const text = source("app/(app)/settings/page.tsx");
  assert.match(text, /key: "general",\s*label: "General"/);
  assert.match(text, /change\(\{ send_key: next \}\)/);
  assert.match(text, /setSendMode\(/);
});
