/**
 * Scenario texts carry figures as placeholders: "{yoy}", "{completion[Урал]}", "{dev:+}".
 * The editor shows them as chips with the figure from the data; the server fills them in.
 */

import type { ValueInfo } from "../api/types";

export interface TextSegment {
  kind: "text";
  text: string;
}

export interface ValueSegment {
  kind: "value";
  token: string; // as written, e.g. "{completion[Урал]}"
  name: string;
  key: string | null;
  signed: boolean;
  label: string | null;
  shown: string | null; // the figure, or null when the name or key is unknown
}

export type Segment = TextSegment | ValueSegment;

// Exactly the server's pattern (backend/app/scenario/text.py, PLACEHOLDER).
const PLACEHOLDER = /\{([a-z][a-z0-9_]*)(?:\[([^\]{}]+)\])?(:\+)?\}/g;

export function segments(text: string, values: ValueInfo[] = []): Segment[] {
  const byName = new Map(values.map((v) => [v.name, v]));
  const result: Segment[] = [];
  let last = 0;
  for (const match of text.matchAll(PLACEHOLDER)) {
    const start = match.index ?? 0;
    if (start > last) result.push({ kind: "text", text: text.slice(last, start) });
    const [token, name = "", key, plus] = match;
    const info = byName.get(name);
    const item = key !== undefined ? findItem(info?.items ?? [], key) : undefined;
    const signed = plus !== undefined;
    let shown: string | null = null;
    if (key !== undefined) shown = item ? (signed ? item.signed : item.value) : null;
    else if (info?.value !== undefined) shown = (signed ? info.signed : info.value) ?? null;
    result.push({
      kind: "value",
      token,
      name,
      key: key ?? null,
      signed,
      label: info?.label ?? null,
      shown,
    });
    last = start + token.length;
  }
  if (last < text.length) result.push({ kind: "text", text: text.slice(last) });
  return result;
}

const normalise = (label: string) =>
  label.toLowerCase().replaceAll("ё", "е").split(/\s+/).filter(Boolean);

/**
 * The server's rule (backend/app/scenario/text.py, series_item): the exact key ignoring case
 * and spacing, else the one item whose key starts with the same words ("Август факт" finds
 * "Август" and the other way round).
 */
export function findItem<T extends { key: string }>(items: T[], key: string): T | undefined {
  const wanted = normalise(key);
  const exact = items.find((i) => normalise(i.key).join(" ") === wanted.join(" "));
  if (exact) return exact;
  const loose = items.filter((i) => {
    const have = normalise(i.key);
    const n = Math.min(have.length, wanted.length);
    return n > 0 && have.slice(0, n).join(" ") === wanted.slice(0, n).join(" ");
  });
  return loose.length === 1 ? loose[0] : undefined;
}

/** The token to insert for a scalar value or one item of a series. */
export function token(name: string, key?: string): string {
  return key === undefined ? `{${name}}` : `{${name}[${key}]}`;
}

/** Insert at the cursor of a text field; returns the new value and where the cursor goes. */
export function insertAt(
  value: string,
  start: number,
  end: number,
  inserted: string,
): { value: string; cursor: number } {
  return {
    value: value.slice(0, start) + inserted + value.slice(end),
    cursor: start + inserted.length,
  };
}
