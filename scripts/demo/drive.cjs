// Drives the demo: Playwright, 1920x1080, with the captions on the page itself so they're burned
// into the video. Writes which parts of the recording to keep, for record.py to cut.
const fs = require("fs");
const path = require("path");

const [, , playwright, url, code, chat, out] = process.argv;
const { chromium } = require(playwright);
const SIZE = { width: 1920, height: 1080 };
const LONG = 240000; // a real model answers in its own time
let started = Date.now(); // reset when the page opens, which is when the video starts
const keep = [];
const seconds = () => (Date.now() - started) / 1000;

async function caption(page, text) {
  await page.evaluate((words) => {
    let box = document.getElementById("demo-caption");
    if (!box) {
      box = document.createElement("div");
      box.id = "demo-caption";
      box.style.cssText = "position:fixed;left:50%;bottom:14px;transform:translateX(-50%);z-index:99999;background:rgba(20,20,24,.92);color:#fff;font:600 26px system-ui,sans-serif;padding:12px 26px;border-radius:16px;max-width:1500px;text-align:center;pointer-events:none";
      document.body.append(box);
    }
    box.textContent = words;
  }, text);
}

// Shows a caption, then keeps `hold` seconds of what follows in the cut.
async function beat(page, name, text, hold, act) {
  await caption(page, text);
  const start = seconds();
  if (act) await act();
  await page.waitForTimeout(hold * 1000);
  await caption(page, text);
  keep.push({ name, start, end: seconds() });
}

// A worker may stop to check something, as a careful one should: answer as the person would.
async function answerQuestions(page) {
  await page.waitForLoadState();
  await page.evaluate(async () => {
    const csrf = decodeURIComponent((document.cookie.match(/(?:^|;\s*)rk_csrf=([^;]+)/) || [])[1] || "");
    const open = await (await fetch("/bff/decisions?status=pending")).json();
    for (const decision of open) {
      if (decision.kind !== "question") continue;
      await fetch(`/bff/decisions/${decision.id}/answer`, {
        method: "POST",
        headers: { "x-csrf-token": csrf, "content-type": "application/json" },
        body: JSON.stringify({ text: "Yes: demo-shop-db, the production one. Go ahead.", by: "owner" }),
      });
    }
  }).catch(() => {}); // the page moved on: the next look will do
}

async function ask(page, words) {
  await page.goto(`${url}/`);
  const box = page.getByPlaceholder(/Ask for something/);
  await box.waitFor();
  await box.fill(words);
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await page.waitForURL(/thread/, { timeout: LONG });
}

(async () => {
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: SIZE, recordVideo: { dir: out, size: SIZE } });
  // Bigger than life, so it reads in a small player and in the README.
  await context.addInitScript(() => {
    document.addEventListener("DOMContentLoaded", () => {
      if (!document.getElementById("phone")) document.documentElement.style.zoom = "1.4";
    });
  });
  const page = await context.newPage();
  started = Date.now();
  page.setDefaultTimeout(LONG);
  await page.goto(`${url}/desktop/start?code=${code}`);
  await page.waitForTimeout(2500);
  await page.evaluate(async () => {
    const csrf = decodeURIComponent((document.cookie.match(/(?:^|;\s*)rk_csrf=([^;]+)/) || [])[1] || "");
    await fetch("/bff/setup/done", { method: "POST", headers: { "x-csrf-token": csrf } });
  });
  await page.goto(`${url}/`);
  await page.waitForTimeout(2000);
  const tour = page.getByRole("button", { name: "Skip the tour" });
  if (await tour.isVisible().catch(() => false)) await tour.click();

  await beat(page, "intro", "Two made-up clients, a real model, and a real gateway in between.", 5);

  // 1. A request routed to the right project.
  await beat(page, "routed", "One chat for every client. Ask in plain words.", 3, async () => {
    await page.getByPlaceholder(/Ask for something/).pressSequentially("Mark order 1007 as shipped in demo-shop-db", { delay: 28 });
  });
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await page.waitForURL(/thread/, { timeout: LONG });
  const goAhead = page.getByRole("button", { name: /Demo shop|Go ahead|Yes/ }).first();
  await goAhead.waitFor();
  await beat(page, "routed", "It finds the right client and project, and checks before touching production.", 9);
  await goAhead.click();

  // 2. A production change held.
  await page.goto(`${url}/approvals`);
  const approve = page.getByRole("button", { name: "Approve", exact: true }).first();
  for (let i = 0; i < 90 && !(await approve.isVisible().catch(() => false)); i++) {
    await answerQuestions(page);
    await page.waitForTimeout(2000);
    await page.reload();
  }
  await beat(page, "held", "Live data: held until you say so.", 13);

  // 3. The approval.
  await beat(page, "approved", "You approve it. Only then does it run.", 3);
  await approve.click();
  const task = await page.evaluate(async () => (await (await fetch("/bff/tasks?limit=1")).json())[0].id);
  await page.goto(`${url}/task?id=${task}`);
  const revert = page.getByRole("button", { name: "Revert", exact: true }).first();
  for (let i = 0; i < 60 && !(await revert.isVisible().catch(() => false)); i++) {
    await page.waitForTimeout(2000);
    await page.reload();
  }
  await beat(page, "approved", "It ran, and the task says what changed.", 5);

  // 4. The undo.
  await beat(page, "undo", "Every change can be undone for 7 days.", 5);
  await revert.click();
  await page.getByRole("button", { name: "Ask to revert" }).click();
  await page.getByText(/Reverting waits for your OK/).waitFor();
  await page.goto(`${url}/approvals`);
  const back = page.getByRole("button", { name: "Approve", exact: true }).first();
  await back.waitFor();
  await beat(page, "undo", "Undoing waits for your OK too.", 5);
  await back.click();
  await page.goto(`${url}/task?id=${task}`);
  const reverted = page.getByText(/^Reverted/).first();
  for (let i = 0; i < 40 && !(await reverted.isVisible().catch(() => false)); i++) {
    await page.waitForTimeout(1500);
    await page.reload();
  }
  await beat(page, "undo", "The old value is back.", 5);

  // 5. A request aimed at the other client's connection, refused.
  await ask(page, "@demo-shop use @acme-orders-db to list the tables");
  await page.getByText(/belongs to|can't|different/).first().waitFor();
  await beat(page, "refused", "Ask for another client's connection, and it's refused.", 16);

  // 6. An approval from Telegram.
  await ask(page, "Mark order 1008 as shipped in demo-shop-db");
  const next = page.getByRole("button", { name: /Demo shop|Go ahead|Yes/ }).first();
  await next.waitFor();
  await next.click();
  for (let i = 0; i < 60; i++) {
    await answerQuestions(page);
    const open = await page.evaluate(async () => (await (await fetch("/bff/decisions?status=pending")).json()).filter((d) => d.kind === "approve").length);
    if (open) break;
    await page.waitForTimeout(2000);
  }
  await page.goto(chat);
  const phone = page.getByRole("button", { name: /Approve/ }).last();
  await phone.waitFor();
  await beat(page, "telegram", "Away from your desk? Approve from Telegram.", 8);
  await beat(page, "telegram", "Approved from your phone.", 8, async () => {
    await phone.click();
  });

  const elapsed = seconds();
  fs.writeFileSync(path.join(out, "keep.json"), JSON.stringify({ elapsed, keep }, null, 1));
  await context.close();
  await browser.close();
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
