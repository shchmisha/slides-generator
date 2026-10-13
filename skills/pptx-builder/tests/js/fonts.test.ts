import assert from "node:assert/strict";
import { test } from "node:test";

import { fontStack } from "../../src/fonts.ts";

test("brand font comes first, metric-compatible substitutes after it", () => {
  assert.equal(fontStack("Arial", "sans-serif"), '"Arial","Liberation Sans",sans-serif');
  assert.equal(fontStack("Georgia", "serif"), '"Georgia","Gelasio","DejaVu Serif",serif');
});

test("a font without known substitutes keeps only the generic fallback", () => {
  assert.equal(fontStack("PT Sans", "sans-serif"), '"PT Sans",sans-serif');
});
