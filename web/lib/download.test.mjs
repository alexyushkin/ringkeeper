// node --test: how a page saves a file it made (save-file.tsx, table-tools.tsx).
import assert from "node:assert/strict";
import { test } from "node:test";

import { dataUrl, fileName, forExcel, saveVia } from "./download.ts";

const MARK = String.fromCharCode(0xfeff);

test("CSV starts with the UTF-8 mark Excel reads, once", () => {
  assert.equal(forExcel("Order,Customer"), `${MARK}Order,Customer`);
  assert.equal(forExcel(forExcel("a,b")), `${MARK}a,b`);
});

test("a browser gets a download link, as before", () => {
  const save = saveVia(false, "table.csv", "a,b\n1,2", "text/csv");
  assert.equal(save.how, "link");
  assert.equal(save.name, "table.csv");
  assert.equal(save.href, dataUrl("a,b\n1,2", "text/csv"));
  assert.equal(decodeURIComponent(save.href.split(",").slice(1).join(",")), "a,b\n1,2");
});

test("the desktop app saves through the backend", () => {
  assert.deepEqual(saveVia(true, "table.csv", "a,b", "text/csv"), { how: "backend", name: "table.csv", text: "a,b" });
});

test("the saved file's name, from a Windows or Mac path", () => {
  assert.equal(fileName("C:\\Users\\Ana\\Downloads\\table (2).csv"), "table (2).csv");
  assert.equal(fileName("/Users/ana/Downloads/report.md"), "report.md");
});
