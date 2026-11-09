// node --test: the table rules markdown.tsx and the task cards use.
import assert from "node:assert/strict";
import { test } from "node:test";

import { firstSentences, readTable, startsTable, withoutTables } from "./markdown-table.ts";

const row = (n) => `| ${n} | Customer ${n} | $${n * 10} |`;

/** A 62-row table as Claude writes it from three pages of a query: a blank line after each page. */
function threePages() {
  const pages = [
    [1, 25],
    [26, 50],
    [51, 62],
  ].map(([a, b]) => Array.from({ length: b - a + 1 }, (_, k) => row(a + k)).join("\n"));
  const text = ["Here are all 62 orders.", "", "| # | Customer | Total |", "|---|---|---|", pages.join("\n\n"), "", "That's every order."];
  return text.join("\n").split("\n");
}

test("a table across three pages stays one table, to its last row", () => {
  const lines = threePages();
  const at = lines.findIndex((_, i) => startsTable(lines, i));
  const table = readTable(lines, at);
  assert.equal(table.rows.length, 62);
  assert.deepEqual(table.rows.at(-1), ["62", "Customer 62", "$620"]);
  assert.equal(lines[table.next + 1], "That's every order.");
});

test("a second table after a blank line is its own", () => {
  const lines = ["| a | b |", "|---|---|", "| 1 | 2 |", "", "| c | d |", "|---|---|", "| 3 | 4 |"];
  assert.deepEqual(readTable(lines, 0).rows, [["1", "2"]]);
  assert.ok(startsTable(lines, 4));
});

test("rows of another width after a blank line aren't joined", () => {
  const lines = ["| a | b |", "|---|---|", "| 1 | 2 |", "", "| x | y | z |"];
  assert.equal(readTable(lines, 0).rows.length, 1);
});

test("a card's preview: the first sentences, without tables or code", () => {
  const text = threePages().join("\n");
  assert.equal(firstSentences(withoutTables(text)), "Here are all 62 orders. That's every order.");
  assert.equal(firstSentences("One. Two. Three."), "One. Two.");
  assert.equal(withoutTables("Before\n```sql\nSELECT 1\n```\nAfter"), "Before\nAfter");
});
