// Ringkeeper's relay for Claude Desktop. Claude starts it on its own Node, and it starts
// Ringkeeper's bridge (`ringkeeper mcp --pass <name>`) from Ringkeeper's install folder.
//
// The bridge exits when Ringkeeper quits, so an update can replace Ringkeeper's files. The
// relay stays: it answers Claude while Ringkeeper is away, starts the bridge again once
// Ringkeeper is back, and repeats Claude's handshake to it, so Claude needn't be restarted.
// It holds no key and decides nothing: every call still goes to Ringkeeper's gateway.
//
// Usage: node relay.js <python> -m ringkeeper mcp --pass <name> --data-dir <folder>
"use strict";

const { spawn } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");

const STOPPED = 75; // the bridge's exit when Ringkeeper stops (bridge.STOPPED)
const WAIT_MS = Number(process.env.RINGKEEPER_RELAY_WAIT_MS || 2000);
const FAILED_STARTS = 3; // in a row, before the relay gives up and Claude shows the error
const AWAY = "Ringkeeper isn't running right now (it may be updating). Try again in a minute.";
const LISTS = new Set(["tools/list", "prompts/list", "resources/list", "resources/templates/list"]);

const [program, ...args] = process.argv.slice(2);
const dataDir = args[args.indexOf("--data-dir") + 1];

let child = null;
let up = false; // Claude's messages go straight to the bridge
let handshake = null; // Claude's initialize request, repeated to a restarted bridge
let replaying = null; // the id of a repeated initialize: its answer is the relay's, not Claude's
let everUp = false;
let failures = 0;
const asked = new Map(); // Claude's requests the bridge hasn't answered: id -> request
const known = new Map(); // the bridge's last full answers to list requests, for while it's away

const say = (text) => process.stderr.write(`Ringkeeper: ${text}\n`);
const toClaude = (message) => process.stdout.write(`${JSON.stringify(message)}\n`);
const toBridge = (message) => child.stdin.write(`${JSON.stringify(message)}\n`);
const isRequest = (m) => typeof m.method === "string" && m.id !== undefined;
const isAnswer = (m) => m.method === undefined && m.id !== undefined;

function lines(stream, each) {
  let rest = "";
  stream.setEncoding("utf8");
  stream.on("data", (chunk) => {
    rest += chunk;
    let end = rest.indexOf("\n");
    while (end >= 0) {
      const line = rest.slice(0, end).trim();
      rest = rest.slice(end + 1);
      end = rest.indexOf("\n");
      if (!line) continue;
      let message;
      try {
        message = JSON.parse(line);
      } catch {
        continue;
      }
      each(message);
    }
  });
}

function fromClaude(message) {
  if (message.method === "initialize") handshake = message;
  if (up) {
    if (isRequest(message)) asked.set(message.id, message);
    toBridge(message);
    return;
  }
  if (!isRequest(message)) return; // a notification while it's away
  const answer = { jsonrpc: "2.0", id: message.id };
  if (message.method === "ping") toClaude({ ...answer, result: {} });
  else if (known.has(message.method)) toClaude({ ...answer, result: known.get(message.method) });
  else toClaude({ ...answer, error: { code: -32000, message: AWAY } });
}

function fromBridge(message) {
  if (replaying !== null && isAnswer(message) && message.id === replaying) {
    replaying = null;
    if (message.error) return; // it exits, and stopped() counts the failure
    toBridge({ jsonrpc: "2.0", method: "notifications/initialized" });
    up = true;
    failures = 0;
    toClaude({ jsonrpc: "2.0", method: "notifications/tools/list_changed" });
    return;
  }
  if (isAnswer(message)) {
    const request = asked.get(message.id);
    asked.delete(message.id);
    if (request && request.method === "initialize" && message.result) {
      everUp = true;
      failures = 0;
    }
    const whole = request && !(request.params && request.params.cursor);
    if (whole && LISTS.has(request.method) && message.result && !message.result.nextCursor) {
      known.set(request.method, message.result);
    }
  }
  toClaude(message);
}

function start(again) {
  const mine = spawn(program, args, { stdio: ["pipe", "pipe", "inherit"], windowsHide: true });
  child = mine;
  let ended = false;
  const end = (code) => {
    if (ended) return;
    ended = true;
    stopped(mine, code);
  };
  mine.on("error", (error) => {
    say(`can't start ${program}: ${error.message}`);
    end(1);
  });
  mine.on("exit", (code) => end(code));
  mine.stdin.on("error", () => {}); // it may already be gone
  lines(mine.stdout, (message) => {
    if (child === mine) fromBridge(message);
  });
  if (again && handshake) {
    replaying = `ringkeeper-relay-${Date.now()}`;
    toBridge({ ...handshake, id: replaying });
  } else {
    up = true;
  }
}

function stopped(which, code) {
  if (which !== child) return;
  child = null;
  up = false;
  replaying = null;
  for (const id of asked.keys()) {
    toClaude({ jsonrpc: "2.0", id, error: { code: -32000, message: AWAY } });
  }
  asked.clear();
  if (code !== STOPPED) {
    failures += 1;
    if (!everUp || failures >= FAILED_STARTS) process.exit(code === null ? 1 : code);
  }
  setTimeout(waitForRingkeeper, WAIT_MS);
}

function running(done) {
  let answered = false;
  const once = (yes) => {
    if (!answered) {
      answered = true;
      done(yes);
    }
  };
  let info;
  try {
    info = JSON.parse(fs.readFileSync(path.join(dataDir, "desktop.json"), "utf8"));
  } catch {
    return once(false); // it writes this file once it's up, and removes it when it quits
  }
  if (typeof info.gateway !== "string") return once(false);
  const asking = http.get(`${info.gateway.replace(/\/$/, "")}/health`, { timeout: 1500 }, (r) => {
    r.resume();
    once(r.statusCode === 200);
  });
  asking.on("timeout", () => asking.destroy());
  asking.on("error", () => once(false));
}

function waitForRingkeeper() {
  running((yes) => {
    if (yes) start(true);
    else setTimeout(waitForRingkeeper, WAIT_MS);
  });
}

lines(process.stdin, fromClaude);
process.stdin.on("end", () => {
  if (child) child.kill();
  process.exit(0);
});
start(false);
