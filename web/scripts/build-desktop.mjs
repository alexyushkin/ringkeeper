// The desktop app's web files: the same app, exported as static files for the
// desktop backend to serve (ringkeeper/desktop/server.py). No Node at runtime.
//
// It builds a copy in web/.desktop without what only the server app has: the
// proxy (its job is the backend's there), the /bff route (the backend is the
// API itself), and the per-item pages (the desktop uses /task?id=... instead).
//
//   node scripts/build-desktop.mjs      ->  web/.desktop/out
import { spawnSync } from "node:child_process";
import { cpSync, existsSync, mkdirSync, readdirSync, rmSync, symlinkSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const web = join(dirname(fileURLToPath(import.meta.url)), "..");
const out = join(web, ".desktop");
// package-lock.json stays out so Next.js takes web/ (with node_modules) as the root.
const SKIP = new Set([
  ".desktop", ".next", "node_modules", "e2e", "playwright-report", "test-results", "Dockerfile",
  "package-lock.json",
]);
const SERVER_ONLY = [
  "proxy.ts",
  "app/bff",
  "app/oauth",
  "lib/server",
  "app/(app)/projects/[name]",
  "app/(app)/tasks/[id]",
  "app/(app)/threads/[id]",
];

rmSync(out, { recursive: true, force: true });
mkdirSync(out);
for (const entry of readdirSync(web)) {
  if (!SKIP.has(entry)) cpSync(join(web, entry), join(out, entry), { recursive: true });
}
for (const path of SERVER_ONLY) rmSync(join(out, path), { recursive: true, force: true });
symlinkSync(join(web, "node_modules"), join(out, "node_modules"), "junction");

const next = join(web, "node_modules", "next", "dist", "bin", "next");
const result = spawnSync(process.execPath, [next, "build"], {
  cwd: out,
  stdio: "inherit",
  env: { ...process.env, NEXT_PUBLIC_RINGKEEPER_TARGET: "desktop", NEXT_TELEMETRY_DISABLED: "1" },
});
if (result.status !== 0) process.exit(result.status ?? 1);
if (!existsSync(join(out, "out", "index.html"))) {
  console.error("the export has no index.html");
  process.exit(1);
}
console.log(`desktop web files: ${join(out, "out")}`);
