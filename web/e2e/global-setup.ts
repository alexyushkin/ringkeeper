import { execFileSync } from "node:child_process";
import { rmSync } from "node:fs";
import path from "node:path";

import { ROOT, compose } from "./stack";

/** A fresh stack every run: no owner, no secrets, empty databases. */
export default async function globalSetup(): Promise<void> {
  execFileSync("docker", ["volume", "create", "ringkeeper_test_stt_models"]);
  compose(["down", "-v", "--remove-orphans"]);
  rmSync(path.join(ROOT, "web", "e2e", ".data"), { recursive: true, force: true });
  compose(["up", "-d", "--build", "--wait", "--wait-timeout", "900"]);
}
