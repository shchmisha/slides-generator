/**
 * SVG stand-in for native charts in previews. It mirrors the PowerPoint chart settings used
 * in emit.ts (same colors, axes, legend position, label formats) so the review screenshot
 * matches the delivered file closely. The PPTX itself always gets a real, editable chart.
 */

import { escapeHtml } from "./html.ts";
import { formatByCode, niceScale } from "./format.ts";
import type { ChartSpec, Theme } from "./types.ts";

const AXIS_FONT = 12;
const LEGEND_H = 30;
const CAT_H = 24;
const AXIS_W = 52;

export interface AxisScale {
  min: number;
  max: number;
  step: number;
}

export interface ChartScales {
  primary: AxisScale;
  secondary?: AxisScale;
}

/**
 * Axis ranges and steps, computed once here and used by both the SVG preview and the native
 * chart (via layout.json), so PowerPoint does not pick different gridlines than the preview.
 * Both axes start at zero unless told otherwise: a 2-point dip must not look like a crash.
 */
export function chartScales(spec: ChartSpec): ChartScales {
  const present = (values: (number | null)[]) => values.filter((v): v is number => v !== null);
  const primaryValues = present(spec.series.filter((s) => s.axis !== "secondary").flatMap((s) => s.values));
  const primary = niceScale(Math.min(0, ...primaryValues), Math.max(0, ...primaryValues));
  const secondaryValues = present(spec.series.filter((s) => s.axis === "secondary").flatMap((s) => s.values));
  if (secondaryValues.length === 0) return { primary };
  const secondary =
    spec.secondaryMin !== undefined && spec.secondaryMax !== undefined
      ? { min: spec.secondaryMin, max: spec.secondaryMax, step: (spec.secondaryMax - spec.secondaryMin) / 5 }
      : niceScale(Math.min(0, ...secondaryValues), Math.max(0, ...secondaryValues));
  return { primary, secondary };
}

export function renderChartSvg(spec: ChartSpec, width: number, height: number, theme: Theme): string {
  const c = theme.colors;
  const fontFamily = escapeHtml(theme.fonts.body);
  const bars = spec.series.filter((s) => s.kind === "bar");
  const lines = spec.series.filter((s) => s.kind === "line");
  const hasSecondary = spec.series.some((s) => s.axis === "secondary");

  const plot = {
    x: AXIS_W,
    y: 10,
    w: width - AXIS_W - (hasSecondary ? AXIS_W : 12),
    h: height - 10 - CAT_H - LEGEND_H,
  };
  const scales = chartScales(spec);
  const p = scales.primary;
  const sec = scales.secondary ?? p;

  const yP = (v: number) => plot.y + plot.h - ((v - p.min) / (p.max - p.min)) * plot.h;
  const yS = (v: number) => plot.y + plot.h - ((v - sec.min) / (sec.max - sec.min)) * plot.h;
  const band = plot.w / spec.categories.length;
  const text = (x: number, y: number, s: string, anchor: string, fill: string, size = AXIS_FONT, weight = 400) =>
    `<text x="${x.toFixed(1)}" y="${y.toFixed(1)}" text-anchor="${anchor}" font-family="${fontFamily}" font-size="${size}" font-weight="${weight}" fill="${fill}">${escapeHtml(s)}</text>`;

  const out: string[] = [];
  // Gridlines + primary axis labels.
  for (let v = p.min; v <= p.max + 1e-9; v += p.step) {
    const y = yP(v);
    out.push(`<line x1="${plot.x}" x2="${plot.x + plot.w}" y1="${y}" y2="${y}" stroke="${c.grid}" stroke-width="1"/>`);
    out.push(text(plot.x - 8, y + 4, formatByCode(v, spec.valueFormat), "end", c.muted));
  }
  if (hasSecondary) {
    for (let v = sec.min; v <= sec.max + 1e-9; v += sec.step) {
      out.push(text(plot.x + plot.w + 8, yS(v) + 4, formatByCode(v, spec.secondaryFormat ?? "0"), "start", c.muted));
    }
  }
  // Bars (clustered).
  const groupW = band * 0.72;
  const barW = bars.length > 0 ? groupW / bars.length : 0;
  bars.forEach((s, si) => {
    s.values.forEach((v, ci) => {
      if (v === null) return; // no data: a gap, never a zero bar
      const x = plot.x + ci * band + (band - groupW) / 2 + si * barW;
      const y = yP(v);
      out.push(
        `<rect x="${(x + 1).toFixed(1)}" y="${y.toFixed(1)}" width="${(barW - 2).toFixed(1)}" height="${(yP(p.min) - y).toFixed(1)}" fill="${c[s.color]}"/>`,
      );
      if (s.showValues) out.push(text(x + barW / 2, y - 5, formatByCode(v, spec.valueFormat), "middle", c.text, 11));
    });
  });
  // Lines.
  for (const s of lines) {
    const y = s.axis === "secondary" ? yS : yP;
    const fmt = s.axis === "secondary" ? (spec.secondaryFormat ?? "0") : spec.valueFormat;
    const pts = s.values.flatMap((v, ci) =>
      v === null ? [] : [[plot.x + ci * band + band / 2, y(v), v] as const],
    );
    out.push(
      `<polyline points="${pts.map(([x, yy]) => `${x.toFixed(1)},${yy.toFixed(1)}`).join(" ")}" fill="none" stroke="${c[s.color]}" stroke-width="2.5"/>`,
    );
    for (const [x, yy, v] of pts) {
      out.push(`<circle cx="${x.toFixed(1)}" cy="${yy.toFixed(1)}" r="4.5" fill="${c[s.color]}" stroke="#fff" stroke-width="1.5"/>`);
      if (s.showValues) out.push(text(x, yy - 10, formatByCode(v, fmt), "middle", c.text, 11, 700));
    }
  }
  // Category labels.
  spec.categories.forEach((cat, ci) => {
    out.push(text(plot.x + ci * band + band / 2, plot.y + plot.h + 17, cat, "middle", c.text));
  });
  // Legend (bottom, centered).
  const items = spec.series.map((s) => ({ name: s.name, color: c[s.color], line: s.kind === "line" }));
  const itemW = items.map((i) => 22 + i.name.length * 6.6 + 18);
  let lx = (width - itemW.reduce((a, b) => a + b, 0)) / 2;
  const ly = height - LEGEND_H / 2;
  items.forEach((item, i) => {
    out.push(
      item.line
        ? `<line x1="${lx}" x2="${lx + 16}" y1="${ly}" y2="${ly}" stroke="${item.color}" stroke-width="2.5"/><circle cx="${lx + 8}" cy="${ly}" r="3.5" fill="${item.color}"/>`
        : `<rect x="${lx}" y="${ly - 6}" width="16" height="12" fill="${item.color}"/>`,
    );
    out.push(text(lx + 22, ly + 4, item.name, "start", c.text));
    lx += itemW[i] ?? 0;
  });

  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">${out.join("")}</svg>`;
}
