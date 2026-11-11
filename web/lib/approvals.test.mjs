// node --test: what an approval card shows without a click, and the "waiting" bar.
import assert from "node:assert/strict";
import { test } from "node:test";

import { collapsed, emailOf, inOrder, plainBody, waitingLabel } from "./approvals.ts";

test("a mail connection's own tool shows To, Subject and the body", () => {
  const args = { to: ["anna@acme.example", "ben@acme.example"], cc: ["c@acme.example"], subject: "Invoice 42", body: "Hi Anna,\nHere it is." };
  assert.deepEqual(emailOf(args), { to: ["anna@acme.example", "ben@acme.example"], cc: ["c@acme.example"], subject: "Invoice 42", body: "Hi Anna,\nHere it is." });
});

test("an Outlook draft or send, from the operation's body", () => {
  const draft = {
    operation: "microsoft.create_draft",
    body: { subject: "Hello", toRecipients: [{ emailAddress: { address: "anna@acme.example", name: "Anna" } }], body: { contentType: "HTML", content: "<p>Hi&nbsp;Anna</p><p>Thanks &amp; bye</p>" } },
  };
  assert.deepEqual(emailOf(draft), { to: ["Anna <anna@acme.example>"], cc: [], subject: "Hello", body: "Hi Anna\nThanks & bye" });
  const send = { operation: "microsoft.send_mail", body: { message: { subject: "Sent", toRecipients: [{ emailAddress: { address: "b@acme.example" } }], body: { contentType: "Text", content: "Plain" } } } };
  assert.equal(emailOf(send)?.body, "Plain");
  assert.deepEqual(emailOf(send)?.to, ["b@acme.example"]);
});

test("anything else isn't an email", () => {
  assert.equal(emailOf({ sql: "UPDATE orders SET status = 'paid'" }), null);
  assert.equal(emailOf({ operation: "stripe.refund", body: { amount: 100 } }), null);
  assert.equal(emailOf(null), null);
});

test("a long body is cut after 12 lines", () => {
  const body = Array.from({ length: 30 }, (_, i) => `line ${i + 1}`).join("\n");
  const cut = collapsed(body);
  assert.equal(cut?.total, 30);
  assert.equal(cut?.shown.split("\n").length, 12);
  assert.equal(collapsed("short\nbody"), null);
});

test("html becomes plain lines", () => {
  assert.equal(plainBody("<div>One</div><div>Two<br>Three</div>"), "One\nTwo\nThree");
});

test("the waiting bar says what's waiting", () => {
  assert.equal(waitingLabel(["approve"]), "1 approval waiting");
  assert.equal(waitingLabel(["approve", "approve"]), "2 approvals waiting");
  assert.equal(waitingLabel(["question"]), "1 question waiting");
  assert.equal(waitingLabel(["plan", "plan"]), "2 plans waiting");
  assert.equal(waitingLabel(["approve", "question", "plan"]), "3 things waiting for you");
});

test("oldest first, so the newest is under what came before", () => {
  const items = [{ id: "b", created_at: "2026-11-11T10:05:00Z" }, { id: "a", created_at: "2026-11-11T10:00:00Z" }];
  assert.deepEqual(inOrder(items).map((i) => i.id), ["a", "b"]);
});
