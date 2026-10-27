import { execFileSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import path from "node:path";

/** The e2e stack: the repo's compose file plus e2e/compose.e2e.yml, as project ringkeeper-e2e. */

export const ROOT = path.resolve(__dirname, "..", "..");
const COMPOSE = ["compose", "-p", "ringkeeper-e2e", "-f", "docker-compose.yml", "-f", "web/e2e/compose.e2e.yml", "--profile", "stt", "--profile", "demo"];

// A throwaway database password (CI sets one for the whole job), never the one in
// a real install's .env.
const DB_PASSWORD = process.env.POSTGRES_PASSWORD || randomBytes(24).toString("hex");

export function compose(args: string[], input?: string): string {
  return execFileSync("docker", [...COMPOSE, ...args], {
    cwd: ROOT,
    env: { ...process.env, POSTGRES_PASSWORD: DB_PASSWORD },
    input,
    encoding: "utf8",
    stdio: [input === undefined ? "ignore" : "pipe", "pipe", "pipe"],
    maxBuffer: 64 * 1024 * 1024,
  });
}

/** Run Python inside the api container (the services layer, the real database). */
export function python(code: string): string {
  return compose(["exec", "-T", "api", "python", "-"], code).trim();
}

/** A query against a demo client database (stands in for a client's Supabase). */
export function demoSql(database: string, sql: string): string {
  return compose(["exec", "-T", "demo-targets", "psql", "-U", "demo", "-d", database, "-tAc", sql]).trim();
}

export function setupCode(): string {
  const logs = compose(["logs", "api"]);
  const codes = [...logs.matchAll(/setup code: ([A-Z0-9]{4}-[A-Z0-9]{4})/g)];
  const code = codes.at(-1)?.[1];
  if (!code) throw new Error("no setup code in the api's log");
  return code;
}
