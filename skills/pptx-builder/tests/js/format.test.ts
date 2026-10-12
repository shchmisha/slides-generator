import assert from "node:assert/strict";
import { test } from "node:test";

import { formatByCode, niceScale } from "../../src/format.ts";

test("Excel format codes render in Russian locale", () => {
  assert.equal(formatByCode(2877, "#,##0"), "2\u00A0877");
  assert.equal(formatByCode(12.58, '0.0"%"'), "12,6\u00A0%");
  assert.equal(formatByCode(375, "0"), "375");
});

test("nice scale covers the data with round steps", () => {
  assert.deepEqual(niceScale(0, 1065), { min: 0, max: 1250, step: 250 });
});
