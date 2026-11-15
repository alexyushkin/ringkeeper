// node --test: a list of checkboxes behaves the same everywhere.
import assert from "node:assert/strict";
import { test } from "node:test";

import { allOn, clearAll, clicked, countText, grouped, matches, noneOn, selectAll, shown } from "./checks.ts";

const rows = [
  { key: "acme-orders", text: "Acme orders", group: "Acme Bakery" },
  { key: "acme-site", text: "Acme site", group: "Acme Bakery" },
  { key: "demo-shop", text: "Demo shop", group: "Demo Co" },
  { key: "gi-copy", text: "Newsletter copy", group: "General Informatics LLC", disabled: true },
  { key: "desk", text: "Your desk" },
];

test("Select all adds every row that can change and keeps the rest", () => {
  assert.deepEqual(selectAll(["other"], rows), ["other", "acme-orders", "acme-site", "demo-shop", "desk"]);
});

test("Clear takes out rows that can change; one that can't keeps its tick", () => {
  assert.deepEqual(clearAll(["acme-site", "gi-copy", "other"], rows), ["gi-copy", "other"]);
});

test("the count is of these rows only", () => {
  assert.equal(countText(["acme-site", "demo-shop", "elsewhere"], rows), "2 of 5 selected");
  assert.equal(countText([], []), "0 of 0 selected");
});

test("all on and none on skip rows that can't change", () => {
  assert.equal(allOn(["acme-orders", "acme-site", "demo-shop", "desk"], rows), true);
  assert.equal(allOn(["acme-orders"], rows), false);
  assert.equal(noneOn(["gi-copy"], rows), true);
  assert.equal(allOn([], [{ key: "x", text: "x", disabled: true }]), false);
});

test("a plain click toggles one row", () => {
  assert.deepEqual(clicked([], rows, "demo-shop", true, "acme-orders", false), ["demo-shop"]);
  assert.deepEqual(clicked(["demo-shop", "desk"], rows, "desk", false, null, false), ["demo-shop"]);
});

test("shift-click sets every row between the two clicks to the clicked row's state", () => {
  assert.deepEqual(clicked(["acme-orders"], rows, "desk", true, "acme-orders", true), ["acme-orders", "acme-site", "demo-shop", "desk"]);
  // upwards, and clearing
  assert.deepEqual(clicked(["acme-orders", "acme-site", "demo-shop", "desk"], rows, "acme-site", false, "desk", true), ["acme-orders"]);
  // with no earlier click, shift is a plain click
  assert.deepEqual(clicked([], rows, "acme-site", true, null, true), ["acme-site"]);
});

test("the filter finds every word typed, in the row or its client", () => {
  assert.equal(matches(rows[0], "acme ORD"), true);
  assert.equal(matches(rows[2], "demo co"), true);
  assert.equal(matches(rows[2], "acme"), false);
  assert.deepEqual(shown(rows, "  ").length, 5);
  assert.deepEqual(shown(rows, "acme").map((r) => r.key), ["acme-orders", "acme-site"]);
});

test("rows are grouped by client in the order they appear, ungrouped last", () => {
  assert.deepEqual(
    grouped(rows).map(([name, list]) => [name, list.map((r) => r.key)]),
    [
      ["Acme Bakery", ["acme-orders", "acme-site"]],
      ["Demo Co", ["demo-shop"]],
      ["General Informatics LLC", ["gi-copy"]],
      ["", ["desk"]],
    ],
  );
});
