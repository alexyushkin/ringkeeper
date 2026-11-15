import { expect, test, type Page } from "@playwright/test";

import { python } from "./stack";

/*
 * Every page that shows work done in the background picks up a finished job while it's
 * open, without a reload. Each test opens a page, finishes something from another process
 * (the api container, as the worker, router or scheduler would), and waits for it on screen.
 *
 * Runs after app.spec.ts, on the stack it set up: the owner, the acme-web project and its
 * acme-database connection.
 */

const PASSWORD = "e2e owner passphrase 1234";
const SHOWN = { timeout: 20_000 }; // live events arrive in well under a second; a poll would be 30+

let n = 0;
function unique(what: string): string {
  n += 1;
  return `${what} ${Date.now().toString(36)}-${n}`;
}

async function signIn(page: Page): Promise<void> {
  await page.goto("/login");
  if (page.url().endsWith("/login")) {
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(page).not.toHaveURL(/\/login$/);
  }
  const skip = page.getByRole("button", { name: "Skip the tour" });
  if (await skip.isVisible().catch(() => false)) await skip.click();
}

/** A finished task in acme-web, from another process; its id. */
function finishedTask(prompt: string, summary: string): string {
  return python(`
from datetime import UTC, datetime
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.models import TaskStatus
from ringkeeper.services import tasks
with session_scope() as session:
    task = tasks.create_task(session, get_settings(), project="acme-web", prompt=${JSON.stringify(prompt)})
    task.status = task.sessions[0].status = TaskStatus.SUCCEEDED
    task.finished_at = datetime.now(UTC)
    task.summary = ${JSON.stringify(summary)}
    print(task.id)
`);
}

/** A write held for approval, from another process (as the gateway holds one). */
function held(sql: string): string {
  return python(`
from ringkeeper.calls import Caller, ToolCall
from ringkeeper.db import session_scope
from ringkeeper.models import InstanceType
from ringkeeper.services import decisions
call = ToolCall(caller=Caller("acme-web", "e2e-live", "e2e-token"), service=InstanceType.SUPABASE,
                tool="execute", instance="acme-database", args={"sql": ${JSON.stringify(sql)}})
with session_scope() as session:
    print(decisions.hold(session, call, "acme-database").id)
`);
}

test.beforeEach(async ({ page }) => {
  await signIn(page);
});

test("Home: a new conversation", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "What do you need?" })).toBeVisible();
  const asked = unique("Count the open invoices");
  python(`
from datetime import UTC, datetime
from ringkeeper.db import session_scope
from ringkeeper.services import threads
with session_scope() as session:
    message = threads.post_message(session, ${JSON.stringify(asked)}, author="owner", source="telegram", target_project="acme-web")
    message.routing, message.routed_at = {"tasks": []}, datetime.now(UTC)
`);
  await expect(page.getByText(asked).first()).toBeVisible(SHOWN);
});

test("Home: Sent for later", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "What do you need?" })).toBeVisible();
  const later = unique("Send the weekly summary");
  python(`
from datetime import UTC, datetime, timedelta
from ringkeeper.db import session_scope
from ringkeeper.services import threads
with session_scope() as session:
    threads.post_message(session, ${JSON.stringify(later)}, author="owner", source="telegram", run_at=datetime.now(UTC) + timedelta(hours=3))
`);
  await expect(page.getByRole("region", { name: "Sent for later" }).getByText(later)).toBeVisible(SHOWN);
});

test("Home: a task cut short by a restart", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "What do you need?" })).toBeVisible();
  const prompt = unique("Tidy the product photos");
  python(`
from datetime import UTC, datetime
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.models import TaskStatus
from ringkeeper.services import interrupted, tasks
with session_scope() as session:
    task = tasks.create_task(session, get_settings(), project="acme-web", prompt=${JSON.stringify(prompt)})
    task.status = task.sessions[0].status = TaskStatus.FAILED
    task.error = interrupted.SAID
    task.finished_at = datetime.now(UTC)
`);
  await expect(page.getByText(prompt).first()).toBeVisible(SHOWN);
});

test("Tasks: a task finished elsewhere", async ({ page }) => {
  await page.goto("/tasks");
  await expect(page.getByRole("heading", { name: "Tasks" })).toBeVisible();
  const summary = `${unique("Updated the price list")}.`; // a card shows the first sentence
  finishedTask(unique("Update the price list"), summary);
  await expect(page.getByText(summary)).toBeVisible(SHOWN);
});

test("A task's page: it finishes while open", async ({ page }) => {
  const id = python(`
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.models import TaskStatus
from ringkeeper.services import tasks
with session_scope() as session:
    task = tasks.create_task(session, get_settings(), project="acme-web", prompt="Check the shipping rates")
    task.status = task.sessions[0].status = TaskStatus.NEEDS_INPUT  # nothing runs it meanwhile
    print(task.id)
`);
  await page.goto(`/tasks/${id}`);
  await expect(page.getByText("Check the shipping rates").first()).toBeVisible();
  const summary = `${unique("The rates are unchanged")}.`;
  python(`
import uuid
from datetime import UTC, datetime
from ringkeeper.db import session_scope
from ringkeeper.models import Task, TaskStatus
with session_scope() as session:
    task = session.get_one(Task, uuid.UUID("${id}"))
    task.status = task.sessions[0].status = TaskStatus.SUCCEEDED
    task.finished_at = datetime.now(UTC)
    task.summary = ${JSON.stringify(summary)}
`);
  await expect(page.getByText(summary)).toBeVisible(SHOWN);
});

test("A conversation: a reply from the router", async ({ page }) => {
  const thread = python(`
from datetime import UTC, datetime
from ringkeeper.db import session_scope
from ringkeeper.services import threads
with session_scope() as session:
    asked = threads.post_message(session, "Which orders shipped today?", author="owner", source="web", target_project="acme-web")
    asked.routing, asked.routed_at = {"tasks": []}, datetime.now(UTC)
    print(asked.thread_id)
`);
  await page.goto(`/threads/${thread}`);
  await expect(page.getByText("Which orders shipped today?").first()).toBeVisible();
  const reply = `${unique("Four orders shipped today")}.`;
  python(`
import uuid
from ringkeeper.db import session_scope
from ringkeeper.services import threads
with session_scope() as session:
    threads.add_reply(session, uuid.UUID("${thread}"), ${JSON.stringify(reply)})
`);
  await expect(page.getByText(reply)).toBeVisible(SHOWN);
});

test("Approvals: a write held while the page is open", async ({ page }) => {
  await page.goto("/approvals");
  await expect(page.getByRole("heading", { name: "Approvals" })).toBeVisible();
  const marker = unique("live-approvals").replace(/\W/g, "_");
  held(`UPDATE customers SET plan = 'pro' WHERE name = '${marker}'`);
  await expect(page.getByText(marker).first()).toBeVisible(SHOWN);
});

test("The menu's Approvals count", async ({ page }) => {
  await page.goto("/tasks");
  // The sidebar's link at this width ("Main" is the phone's bottom bar).
  const link = page.locator("aside").getByRole("link", { name: /^Approvals/ });
  await expect(link).toBeVisible();
  const badge = link.getByLabel(/waiting$/);
  const before = (await badge.count()) ? Number(await badge.textContent()) : 0;
  held(`UPDATE customers SET plan = 'pro' WHERE name = '${unique("live-badge").replace(/\W/g, "_")}'`);
  await expect(badge).toHaveText(String(before + 1), SHOWN);
});

test("A project's Tasks tab", async ({ page }) => {
  await page.goto("/projects/acme-web#tasks");
  await page.getByRole("tab", { name: /Tasks/ }).click();
  const summary = `${unique("Renamed the hero image")}.`;
  finishedTask(unique("Rename the hero image"), summary);
  await expect(page.getByText(summary)).toBeVisible(SHOWN);
});

test("Connectors: the connection check's result", async ({ page }) => {
  // Every page's data is fetched again when background work ends (a "job" event): here the
  // daily connection check, written as the scheduler writes it.
  await page.goto("/connections");
  await expect(page.getByRole("heading", { name: "Connectors" })).toBeVisible();
  const said = unique("Password authentication failed");
  python(`
from datetime import UTC, datetime
from sqlalchemy import select
from ringkeeper.db import session_scope
from ringkeeper.models import Instance
from ringkeeper.services import jobs
with session_scope() as session:
    found = session.scalar(select(Instance).where(Instance.name == "acme-database"))
    found.last_test_ok, found.last_test_message, found.last_test_at = False, ${JSON.stringify(said)}, datetime.now(UTC)
    jobs.changed(session, jobs.HEALTH)
`);
  await expect(page.getByText(said).first()).toBeVisible(SHOWN);
  python(`
from sqlalchemy import select
from ringkeeper.db import session_scope
from ringkeeper.models import Instance
with session_scope() as session:
    found = session.scalar(select(Instance).where(Instance.name == "acme-database"))
    found.last_test_ok, found.last_test_message = True, "Connected"
`);
});

test.afterEach(async ({ page }, info) => {
  if (info.status !== info.expectedStatus) {
    await page.screenshot({ path: info.outputPath("failure.png"), fullPage: true });
  }
});
