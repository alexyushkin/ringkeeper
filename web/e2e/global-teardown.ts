import { compose } from "./stack";

/** Leaves the stack up with E2E_KEEP=1 (to look around after a failure). */
export default async function globalTeardown(): Promise<void> {
  if (process.env.E2E_KEEP !== "1") compose(["down", "-v", "--remove-orphans"]);
}
