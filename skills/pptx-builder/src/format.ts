/**
 * Formats a number the way a simple Excel format code would, in Russian locale.
 * Supported subset (all we emit): `#,##0`, `0`, `0.0`, `#,##0.0`, optional quoted suffix
 * such as `0.0"%"`. Used for SVG previews so they read like the native PowerPoint chart.
 */
export function formatByCode(value: number, code: string): string {
  const suffix = code.match(/"([^"]*)"/)?.[1] ?? "";
  const numeric = code.replace(/"[^"]*"/g, "");
  const decimals = numeric.includes(".") ? (numeric.split(".")[1]?.replace(/[^0#]/g, "").length ?? 0) : 0;
  const grouping = numeric.includes(",");
  const text = new Intl.NumberFormat("ru-RU", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
    useGrouping: grouping,
  })
    .format(value)
    .replace(/\u202F/g, "\u00A0"); // narrow nbsp is missing in many fonts
  return suffix ? `${text}${suffix === "%" ? "\u00A0%" : suffix}` : text;
}

/** A "nice" axis maximum and step for 4–6 gridlines. */
export function niceScale(min: number, max: number, ticks = 5): { min: number; max: number; step: number } {
  const span = max - min || Math.abs(max) || 1;
  const raw = span / ticks;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const norm = raw / mag;
  const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10) * mag;
  return { min: Math.floor(min / step) * step, max: Math.ceil(max / step) * step, step };
}
