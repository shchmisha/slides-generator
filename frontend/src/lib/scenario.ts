/** Helpers for editing scenarios in the browser; the server checks every saved version. */

import type { Json } from "../api/types";

export const SLIDE_TYPES: Record<string, string> = {
  key_takeaways: "Главное",
  chart_table: "График и таблица",
  chart: "График",
  table: "Таблица",
  cost_structure: "Структура затрат",
  risks_decisions: "Риски и решения",
  data_notes: "Проверка данных",
};

export const VISUAL_TYPES: Record<string, string> = {
  title: "Заставка",
  statement: "Тезис",
  bullets: "Список",
  big_number: "Крупная цифра",
  comparison: "Сравнение",
  steps: "Шаги",
  closing: "Финал",
};

export function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

export function same(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

export function move<T>(list: T[], from: number, to: number): void {
  if (to < 0 || to >= list.length || from === to) return;
  const [item] = list.splice(from, 1);
  if (item !== undefined) list.splice(to, 0, item);
}

/** Server errors name a path ("slides[2].title: …"): group them by slide or scene. */
export function errorsByIndex(
  errors: string[],
  collection: "slides" | "scenes",
): {
  byIndex: Map<number, string[]>;
  general: string[];
} {
  const byIndex = new Map<number, string[]>();
  const general: string[] = [];
  const pattern = new RegExp(`^${collection}\\[(\\d+)\\]`);
  for (const error of errors) {
    const match = pattern.exec(error);
    if (match) {
      const index = Number(match[1]);
      byIndex.set(index, [...(byIndex.get(index) ?? []), error]);
    } else {
      general.push(error);
    }
  }
  return { byIndex, general };
}

/**
 * Server errors are written for the model that wrote the scenario ("add it to values…").
 * For a person, the common ones are retold in terms of the editor; the field path is kept.
 */
export function humanize(error: string): string {
  const [path = "", ...rest] = error.split(": ");
  const text = rest.join(": ");
  const typed = /^числ\S* (.+?) написан\S* вручную/.exec(text);
  if (typed) {
    return `${path}: ${typed[1]} — цифры нельзя вписывать вручную. Поставьте курсор и выберите значение в списке «Значения из данных»`;
  }
  if (text.includes("в фигурных скобках не подстановка")) {
    return `${path}: число в фигурных скобках — не значение из данных. Выберите значение в списке «Значения из данных»`;
  }
  // Model-facing advice after the first sentence ("Каждую цифру из данных вставляйте…").
  return error.replace(/\. (Каждую цифру|Числа из документов|N показателя)[^]*$/, "");
}

const FIELD_LABELS: [RegExp, (n: number) => string][] = [
  [/^title$/, () => "Заголовок"],
  [/^subtitle$/, () => "Подзаголовок"],
  [/^callout$/, () => "Вывод внизу слайда"],
  [/^total_label$/, () => "Подпись итога"],
  [/^takeaways\[(\d+)\]$/, (n) => `Вывод ${n}`],
  [/^takeaways$/, () => "Выводы"],
  [/^kpis\[(\d+)\]\.label$/, (n) => `Показатель ${n}: подпись`],
  [/^kpis\[(\d+)\]\.delta$/, (n) => `Показатель ${n}: пояснение`],
  [/^risks\[(\d+)\]\.title$/, (n) => `Риск ${n}: кратко`],
  [/^risks\[(\d+)\]\.text$/, (n) => `Риск ${n}: подробнее`],
  [/^decisions\[(\d+)\]\.title$/, (n) => `Решение ${n}: кратко`],
  [/^decisions\[(\d+)\]\.text$/, (n) => `Решение ${n}: подробнее`],
  [/^chart\.caption$/, () => "Подпись графика"],
  [/^chart\.series\[(\d+)\]\.name$/, (n) => `Ряд ${n}`],
  [/^table\.row_header$/, () => "Заголовок первого столбца"],
  [/^table\.columns\[(\d+)\]\.header$/, (n) => `Столбец ${n}`],
  [/^table\.total_label$/, () => "Строка итога"],
  [/^narration$/, () => "Текст диктора"],
  [/^duration_s$/, () => "Длительность"],
  [/^visual\.heading$/, () => "Заголовок"],
  [/^visual\.number$/, () => "Цифра"],
  [/^visual\.caption$/, () => "Подпись"],
  [/^visual\.items\[(\d+)\]$/, (n) => `Пункт ${n}`],
];

/** The field of a slide or scene an error is about: "slides[2].kpis[0].label: …" → "kpis[0].label". */
export function fieldOf(error: string): string {
  return (error.split(": ")[0] ?? "").replace(/^(slides|scenes)\[\d+\]\.?/, "");
}

/** The error for a card, the field named as in the form: "Вывод 2: …". */
export function shortPath(error: string): string {
  const text = humanize(error);
  const field = fieldOf(error);
  const message = text.slice(text.indexOf(": ") + 2);
  for (const [pattern, label] of FIELD_LABELS) {
    const match = pattern.exec(field);
    if (match) return `${label(Number(match[1] ?? 0) + 1)}: ${message}`;
  }
  return text.replace(/^(slides|scenes)\[\d+\]\.?/, "");
}

export const asList = (value: unknown): Json[] =>
  Array.isArray(value) ? (value.filter((v) => v && typeof v === "object") as Json[]) : [];
