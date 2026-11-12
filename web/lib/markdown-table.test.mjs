// node --test: the table rules markdown.tsx and the task cards use.
import assert from "node:assert/strict";
import { test } from "node:test";

import { firstSentences, mainTable, plainCell, readTable, startsTable, toCsv, toTsv, withoutTables } from "./markdown-table.ts";

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

test("a table copies as tab-separated lines and downloads as CSV, without Markdown marks", () => {
  const head = ["#", "Workflow", "Notes"];
  const rows = [
    ["1", "**Orders sync**", "runs at 9, then at 5"],
    ["2", "[Invoices](https://example.test/w/2)", 'says "paid"'],
  ];
  assert.equal(plainCell("**bold** and `code` and ~~gone~~"), "bold and code and gone");
  assert.equal(toTsv(head, rows), "#\tWorkflow\tNotes\n1\tOrders sync\truns at 9, then at 5\n2\tInvoices\tsays \"paid\"");
  assert.equal(toCsv(head, rows), '#,Workflow,Notes\r\n1,Orders sync,"runs at 9, then at 5"\r\n2,Invoices,"says ""paid"""\r\n');
});

test("a 105-row table downloads every row", () => {
  const lines = ["| # | Name |", "|---|---|", ...Array.from({ length: 105 }, (_, i) => `| ${i + 1} | Row ${i + 1} |`)];
  const { head, rows } = readTable(lines, 0);
  const csv = toCsv(head, rows).trimEnd().split("\r\n");
  assert.equal(csv.length, 106);
  assert.equal(csv.at(-1), "105,Row 105");
});

test("a report's table, for Save table as CSV", () => {
  const report = "# Check my setup, 2026-11-11\r\n\r\n2 of 2 worked.\r\n\r\n| Request | Cost |\r\n| --- | --- |\r\n| **Orders** in Acme | $0.04 |\r\n| A web lookup | $0.02 |\r\n\r\n## Gaps\r\n\r\nNone.";
  const table = mainTable(report);
  assert.deepEqual(table?.head, ["Request", "Cost"]);
  assert.equal(table?.rows.length, 2);
  assert.equal(toCsv(table.head, table.rows), "Request,Cost\r\nOrders in Acme,$0.04\r\nA web lookup,$0.02\r\n");
  assert.equal(mainTable("# No table here\n\nJust words."), null);
  const selfTest = "| | |\n|---|---|\n| Holds fired | 4 |\n\n| Scenario | Result |\n|---|---|\n| read-tables | pass |\n| ambiguous | pass |\n| split | fail |";
  assert.deepEqual(mainTable(selfTest)?.head, ["Scenario", "Result"]); // not the summary above it
});
