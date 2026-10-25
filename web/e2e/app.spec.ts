import { expect, test, type Page } from "@playwright/test";

import { demoSql, python, setupCode } from "./stack";

/*
 * One run through the app on a fresh stack, in order: the wizard, a message,
 * approving held writes (an ordinary one and one that deletes data), a
 * transcript, a voice request through the real speech service, and the
 * phone layout at 375 px.
 */

const PASSWORD = "e2e owner passphrase 1234";
const PROD_DSN = "postgresql://demo:demo-local-only@demo-targets:5432/acme_prod";

function hold(sql: string): string {
  return python(`
from ringkeeper.calls import Caller, ToolCall
from ringkeeper.db import session_scope
from ringkeeper.models import InstanceType
from ringkeeper.services import decisions
call = ToolCall(caller=Caller("acme-web", "e2e-session", "e2e-token"), service=InstanceType.SUPABASE,
                tool="execute", instance="acme-database", args={"sql": ${JSON.stringify(sql)}})
with session_scope() as session:
    print(decisions.hold(session, call, "acme-database").id)
`);
}

function decisionStatus(id: string): string {
  return python(`
from ringkeeper.db import session_scope
from ringkeeper.services import decisions
with session_scope() as session:
    print(decisions.get_decision(session, "${id}").status.value)
`);
}

async function waitFor(check: () => string, expected: string): Promise<void> {
  await expect.poll(check, { timeout: 60_000, intervals: [500, 1000, 2000] }).toBe(expected);
}

test("the whole app on a fresh stack", async ({ page }) => {
  await test.step("the wizard: login, recovery key, Claude, a project and a working connection", async () => {
    await page.goto("/");
    await expect(page).toHaveURL(/\/setup$/);
    await page.getByLabel("Setup code").fill("WRNG-CODE");
    await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
    await page.getByLabel("Password again").fill(PASSWORD);
    await page.getByRole("button", { name: "Create login" }).click();
    await expect(page.getByText("That setup code isn't right")).toBeVisible();

    await page.getByLabel("Setup code").fill(setupCode());
    await page.getByRole("button", { name: "Create login" }).click();
    await expect(page.getByText("If you lose this file, you'll need to re-enter your connection secrets.")).toBeVisible();
    const download = page.waitForEvent("download");
    await page.getByRole("button", { name: "Download recovery key" }).click();
    expect((await download).suggestedFilename()).toBe("ringkeeper-recovery-key.txt");
    await page.getByLabel("I saved the recovery key somewhere safe").check();
    await page.getByRole("button", { name: "Continue" }).click();

    await page.getByLabel("Anthropic API key").fill("sk-ant-api03-wrong-key-000000000000000000");
    await page.getByRole("button", { name: "Check and save" }).click();
    await expect(page.getByText("didn't accept this key")).toBeVisible();
    await page.getByLabel("Anthropic API key").fill("sk-ant-api03-e2e-0000000000000000000000000");
    await page.getByRole("button", { name: "Check and save" }).click();
    await expect(page.getByText("Anthropic accepted the key.")).toBeVisible();
    await page.getByRole("button", { name: "Continue" }).click();

    await page.getByRole("button", { name: "Skip for now" }).click();

    await page.getByLabel("Client", { exact: true }).fill("Acme");
    await page.getByLabel("Project name").fill("Acme web");
    await page.getByLabel("What is it?").fill("Acme's customer website and its plans.");
    await page.getByLabel("Folder").fill("acme-web");
    await page.getByRole("button", { name: "Add project" }).click();

    await page.getByLabel("Name", { exact: true }).fill("Acme database");
    await page.getByLabel("Client", { exact: true }).fill("Acme");
    await page.getByLabel("Kind of data").selectOption("prod");
    await page.getByLabel("Connection string").fill(PROD_DSN);
    await page.getByRole("button", { name: "Add connection" }).click();
    await expect(page.getByText("Added acme-database.")).toBeVisible();
    await page.getByRole("button", { name: "Test connection" }).click();
    await expect(page.getByText("Connected: read 1 table.")).toBeVisible();
    await page.getByRole("button", { name: "Connect it to acme-web and finish" }).click();
    await page.getByRole("link", { name: "Ask for something" }).click();
    await expect(page.getByRole("heading", { name: "What do you need?" })).toBeVisible();
    expect(await page.content()).not.toContain("demo-local-only");
  });

  await test.step("sending a message", async () => {
    await page.getByLabel("Request").fill("How many customers does Acme have on the pro plan?");
    await page.getByRole("button", { name: "Send", exact: true }).click();
    await expect(page).toHaveURL(/\/threads\//);
    await expect(page.getByRole("heading", { name: "How many customers does Acme have on the pro plan?" })).toBeVisible();
    await expect(page.getByText("Finding the right project…")).toBeVisible();
  });

  await test.step("approving a held prod write, then one that deletes data", async () => {
    const update = hold("UPDATE customers SET plan = 'enterprise' WHERE name = 'Coyote LLC'");
    await page.goto("/approvals");
    const card = page.locator(`[id="${update}"]`).locator("..");
    await expect(card.getByText("UPDATE customers SET plan = 'enterprise'")).toBeVisible();
    await card.getByRole("button", { name: "Approve" }).click();
    await waitFor(() => decisionStatus(update), "executed");
    expect(demoSql("acme_prod", "SELECT plan FROM customers WHERE name = 'Coyote LLC'")).toBe("enterprise");

    const destructive = hold("DELETE FROM customers WHERE name = 'Tunnel Paint Co'");
    await page.reload();
    const risky = page.locator(`[id="${destructive}"]`).locator("..");
    await expect(risky.getByText("This change deletes rows. That can't be undone.")).toBeVisible();
    const approve = risky.getByRole("button", { name: "Approve" });
    await expect(approve).toBeDisabled();
    await risky.getByLabel("Type acme-database to approve").fill("acme-database");
    await approve.click();
    await waitFor(() => decisionStatus(destructive), "executed");
    expect(demoSql("acme_prod", "SELECT count(*) FROM customers WHERE name = 'Tunnel Paint Co'")).toBe("0");
  });

  await test.step("opening a transcript", async () => {
    const taskId = python(`
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.models import TaskStatus
from ringkeeper.services import tasks
with session_scope() as session:
    task = tasks.create_task(session, get_settings(), project="acme-web", prompt="Add a notes line")
    task.status = task.sessions[0].status = TaskStatus.SUCCEEDED
    task.summary = "Added the line to NOTES.md."
    first = task.sessions[0]
    tasks.add_event(session, first.id, 1, "sdk.assistant", {"message": {"content": [
        {"_kind": "TextBlock", "text": "I'll add the line now."},
        {"_kind": "ToolUseBlock", "name": "Edit", "input": {"file_path": "NOTES.md", "new_string": "voice works"}}]}})
    tasks.add_event(session, first.id, 2, "sdk.user", {"message": {"content": [
        {"_kind": "ToolResultBlock", "content": "The file NOTES.md has been updated."}]}})
    tasks.add_event(session, first.id, 3, "ringkeeper.diff", {"text": "+voice works"})
    print(task.id)
`);
    await page.goto("/tasks");
    await page.getByText("Added the line to NOTES.md.").click();
    await expect(page).toHaveURL(new RegExp(`/tasks/${taskId}$`));
    await expect(page.getByText("I'll add the line now.")).toBeVisible();
    await expect(page.getByText("Used Edit")).toBeVisible();
    await page.getByRole("button", { name: "Show arguments" }).click();
    await expect(page.getByText('"new_string": "voice works"')).toBeVisible();
    await page.getByRole("button", { name: /Changes/ }).click();
    await expect(page.getByText("+voice works")).toBeVisible();
  });

  await test.step("a voice request through the real speech service", async () => {
    await page.goto("/");
    const mic = page.getByRole("button", { name: "Hold to talk" });
    const box = await mic.boundingBox();
    if (!box) throw new Error("no mic button");
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.waitForTimeout(4500); // the fixture says it in 3.3 s
    await page.mouse.up();
    const heard = page.getByText("I heard:").locator("..");
    // Synthetic speech through a fake microphone and the small "base" model: the
    // robotic "Voice works" varies by machine ("Moise works", "My swergs"), while
    // "add a line to the notes" comes through every time.
    await expect(heard).toContainText(/add (a )?line to the notes/i, { timeout: 120_000 });
    await page.getByRole("button", { name: "Send", exact: true }).click();
    await expect(page).toHaveURL(/\/threads\//);
    await expect(page.getByText(/Spoken/)).toBeVisible();
  });

  await test.step("the phone layout at 375 px, and the app installs", async () => {
    await page.setViewportSize({ width: 375, height: 812 });
    for (const path of ["/", "/approvals", "/tasks", "/projects", "/connections", "/activity", "/settings"]) {
      await page.goto(path);
      await page.waitForLoadState("networkidle");
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      expect(overflow, `${path} scrolls sideways`).toBeLessThanOrEqual(0);
      await expect(page.getByRole("navigation", { name: "Main" })).toBeVisible();
    }
    await page.getByRole("button", { name: "More" }).click();
    await expect(page.getByRole("link", { name: "Connections" })).toBeVisible();

    const manifest = await (await page.request.get("/manifest.webmanifest")).json();
    expect(manifest.display).toBe("standalone");
    await expect.poll(() => page.evaluate(async () => (await navigator.serviceWorker.getRegistrations()).length)).toBeGreaterThan(0);
  });
});

test.afterEach(async ({ page }: { page: Page }, info) => {
  if (info.status !== info.expectedStatus) {
    await page.screenshot({ path: info.outputPath("failure.png"), fullPage: true });
  }
});
