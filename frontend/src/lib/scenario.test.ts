import { describe, expect, it } from "vitest";

import { errorsByIndex, fieldOf, humanize, move, same, shortPath } from "./scenario";

describe("scenario helpers", () => {
  it("moves items within bounds only", () => {
    const list = ["a", "b", "c"];
    move(list, 0, 2);
    expect(list).toEqual(["b", "c", "a"]);
    move(list, 0, -1);
    move(list, 2, 3);
    expect(list).toEqual(["b", "c", "a"]);
  });

  it("groups server errors by slide", () => {
    const { byIndex, general } = errorsByIndex(
      ["slides[2].title: слишком длинно", "slides[2].callout: число «12»", "claims[0].quote: нет"],
      "slides",
    );
    expect(byIndex.get(2)).toHaveLength(2);
    expect(general).toEqual(["claims[0].quote: нет"]);
    expect(shortPath("slides[2].callout: число «12»")).toBe("Вывод внизу слайда: число «12»");
    expect(shortPath("slides[0].kpis[1].label: обязательное поле")).toBe(
      "Показатель 2: подпись: обязательное поле",
    );
    expect(fieldOf("slides[0].kpis[1].label: обязательное поле")).toBe("kpis[1].label");
  });

  it("compares drafts by content", () => {
    expect(same({ a: [1, { b: 2 }] }, { a: [1, { b: 2 }] })).toBe(true);
    expect(same({ a: 1 }, { a: 2 })).toBe(false);
  });
});

describe("humanize", () => {
  it("retells model-facing errors for a person", () => {
    const raw =
      "slides[0].takeaways[0]: числа «999» написаны вручную. Каждую цифру из данных вставляйте " +
      "через {имя}: добавьте значение в values или расчёт в derived.";
    const text = humanize(raw);
    expect(text).toMatch(/^slides\[0\]\.takeaways\[0\]: «999» — цифры нельзя вписывать вручную/);
    expect(text).not.toMatch(/values|derived/);
    expect(shortPath(raw)).toMatch(/^Вывод 1: «999» — цифры нельзя/);
    expect(humanize("claims[0].quote: такой фразы нет")).toBe("claims[0].quote: такой фразы нет");
  });
});
