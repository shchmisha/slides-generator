import assert from "node:assert/strict";
import { test } from "node:test";

import { formatByCode, niceScale } from "../../src/format.ts";
import { withHeadroom } from "../../src/preview_chart.ts";

test("Excel format codes render in Russian locale", () => {
  assert.equal(formatByCode(2877, "#,##0"), "2\u00A0877");
  assert.equal(formatByCode(12.58, '0.0"%"'), "12,6\u00A0%");
  assert.equal(formatByCode(375, "0"), "375");
});

test("nice scale covers the data with round steps", () => {
  assert.deepEqual(niceScale(0, 1065), { min: 0, max: 1250, step: 250 });
});

test("a labelled value near the top of the axis gets one more step", () => {
  // Sept revenue 998 on an axis ending at 1 000: its label was cut off above the plot.
  const scale = niceScale(0, 998);
  assert.deepEqual(scale, { min: 0, max: 1000, step: 200 });
  assert.deepEqual(withHeadroom(scale, 998, true), { min: 0, max: 1200, step: 200 });
  assert.deepEqual(withHeadroom(scale, 998, false), scale); // no label, no extra space
  assert.deepEqual(withHeadroom(niceScale(0, 1065), 1065, true), { min: 0, max: 1250, step: 250 });
});
