import { describe, expect, it } from "vitest";

import type { ValueInfo } from "../api/types";
import { findItem, insertAt, segments, token } from "./placeholders";

const VALUES: ValueInfo[] = [
  { name: "rev_fact", label: "Выручка факт", value: "2 877 млн ₽", signed: "+2 877 млн ₽" },
  {
    name: "completion",
    label: "Выполнение плана",
    items: [{ key: "Урал", value: "96,6 %", signed: "−3,4 %" }],
  },
];

describe("segments", () => {
  it("shows figures in place of placeholders", () => {
    const parts = segments("Выручка {rev_fact}, Урал {completion[Урал]}.", VALUES);
    expect(parts.map((p) => (p.kind === "text" ? p.text : p.shown))).toEqual([
      "Выручка ",
      "2 877 млн ₽",
      ", Урал ",
      "96,6 %",
      ".",
    ]);
  });

  it("uses the signed form for {name:+}", () => {
    const [part] = segments("{rev_fact:+}", VALUES);
    expect(part).toMatchObject({ kind: "value", signed: true, shown: "+2 877 млн ₽" });
  });

  it("marks unknown names and keys", () => {
    const parts = segments("{nope} {completion[Марс]}", VALUES);
    expect(
      parts.filter((p) => p.kind === "value").map((p) => p.kind === "value" && p.shown),
    ).toEqual([null, null]);
  });

  it("leaves text without placeholders alone", () => {
    expect(segments("Риски и решения")).toEqual([{ kind: "text", text: "Риски и решения" }]);
  });
});

describe("token and insertAt", () => {
  it("build placeholders and insert them at the cursor", () => {
    expect(token("rev_fact")).toBe("{rev_fact}");
    expect(token("completion", "Урал")).toBe("{completion[Урал]}");
    expect(insertAt("Выручка .", 8, 8, "{rev_fact}")).toEqual({
      value: "Выручка {rev_fact}.",
      cursor: 18,
    });
    expect(insertAt("abc", 1, 2, "X").value).toBe("aXc");
  });
});

describe("findItem", () => {
  const items = [
    { key: "Июль", value: "12,4 %" },
    { key: "Август", value: "11,1 %" },
    { key: "Итого по компании", value: "12,6 %" },
  ];

  it("matches keys the way the server does", () => {
    expect(findItem(items, "август")?.value).toBe("11,1 %");
    expect(findItem(items, "Август факт")?.value).toBe("11,1 %");
    expect(findItem(items, "Итого  по компании")?.value).toBe("12,6 %");
    expect(findItem(items, "Итого")?.value).toBe("12,6 %");
    const both = [
      { key: "Июль факт", value: "1" },
      { key: "Июль план", value: "2" },
    ];
    expect(findItem(both, "Июль")).toBeUndefined(); // two candidates: no guess
    expect(findItem(items, "Сентябрь")).toBeUndefined();
  });
});
