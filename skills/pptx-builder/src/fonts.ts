import substitutes from "../fonts/substitutes.json" with { type: "json" };

/**
 * Brand fonts are rarely installed where the layout runs (a Linux container), yet PowerPoint
 * will draw the text with them. Listing a free font with the same metrics right after the brand
 * font makes Chromium break lines exactly as PowerPoint will; a machine that has the brand font
 * still uses it, and the emitter takes only the first name.
 *
 * Georgia: Gelasio has its metrics but no Cyrillic, so Cyrillic falls through to DejaVu Serif,
 * which is 8–14 % wider than Georgia: a Russian heading may wrap or shrink a little earlier
 * than needed, never overflow in PowerPoint. (Liberation Serif, the default fallback, is up to
 * 17 % narrower than Georgia Bold.)
 */
const SUBSTITUTES: Readonly<Record<string, readonly string[]>> = substitutes as unknown as Record<
  string,
  readonly string[]
>;

export function fontStack(family: string, generic: "serif" | "sans-serif"): string {
  const names = [family, ...(SUBSTITUTES[family.toLowerCase()] ?? [])];
  return [...names.map((name) => `"${name}"`), generic].join(",");
}
