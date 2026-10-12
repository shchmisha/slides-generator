import assert from "node:assert/strict";
import { test } from "node:test";

import { html, raw, typography } from "../../src/html.ts";

const NBSP = "\u00A0";

test("interpolated text is escaped, raw markup is not", () => {
  const out = html`<p>${'<img src=x onerror="alert(1)">'}</p>${raw("<b>ok</b>")}`;
  assert.equal(String(out), "<p>&lt;img src=x onerror=&quot;alert(1)&quot;&gt;</p><b>ok</b>");
});

test("typography keeps numbers, units and dashes together", () => {
  assert.equal(typography("2 877 млн ₽"), `2${NBSP}877${NBSP}млн${NBSP}₽`);
  assert.equal(typography("рост 8,2 %"), `рост 8,2${NBSP}%`);
  assert.equal(typography("1 050 000"), `1${NBSP}050${NBSP}000`);
  assert.equal(typography("Урал — ниже плана"), `Урал${NBSP}— ниже плана`);
});

test("short prepositions stick to the next word, even in a row", () => {
  assert.equal(typography("рост и в сентябре"), `рост и${NBSP}в${NBSP}сентябре`);
});

test("typography leaves ordinary spaces alone", () => {
  assert.equal(typography("план 3 квартала 5 филиалов"), "план 3 квартала 5 филиалов");
  assert.equal(typography("12 млнов"), "12 млнов");
});
