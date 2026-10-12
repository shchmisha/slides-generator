import assert from "node:assert/strict";
import { test } from "node:test";

import { COLOR_TOKENS, parseTheme } from "../../src/theme.ts";

const colors = Object.fromEntries(COLOR_TOKENS.map((t) => [t, "#1f3a5f"]));

test("valid theme is normalized", () => {
  const theme = parseTheme({ name: "brand", colors, fonts: { heading: "Georgia", body: "PT Sans" } });
  assert.equal(theme.colors.primary, "#1F3A5F");
  assert.equal(theme.fonts.body, "PT Sans");
});

test("CSS injection through colors or fonts is rejected", () => {
  assert.throws(() =>
    parseTheme({ colors: { ...colors, accent: "red;}</style><script>" }, fonts: { heading: "A", body: "B" } }),
  );
  assert.throws(() => parseTheme({ colors, fonts: { heading: 'Arial",serif;}', body: "B" } }));
  assert.throws(() => parseTheme({ colors: { primary: "#000000" }, fonts: { heading: "A", body: "B" } }));
});
