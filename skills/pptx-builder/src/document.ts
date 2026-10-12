import { html } from "./html.ts";
import { renderSlide } from "./templates/index.ts";
import type { DeckSpec, Theme } from "./types.ts";

export const SLIDE_W_PX = 1280;
export const SLIDE_H_PX = 720;

function themeVars(theme: Theme): string {
  const colors = Object.entries(theme.colors)
    .map(([token, value]) => `--${token}:${value};`)
    .join("");
  return `:root{${colors}--font-heading:"${theme.fonts.heading}",serif;--font-body:"${theme.fonts.body}",sans-serif;}`;
}

/**
 * Layout rules for every slide type. Sizes are CSS px on a 1280×720 canvas
 * (= 13.333×7.5 in at 96 px/in, the PowerPoint 16:9 size).
 *
 * Elements that become PowerPoint objects carry `data-pptx`:
 *   text  — text box (inline <b>/<span> styles become runs)
 *   shape — filled/bordered rectangle, rounded rectangle or ellipse
 *   image — picture
 *   chart — native chart drawn into this box (spec in data-chart)
 *   table — native table measured cell by cell
 * `data-fit="shrink"` lets the layout engine reduce the font size until the text fits.
 */
const BASE_CSS = `
*{box-sizing:border-box;margin:0;padding:0}
html,body{background:#d9dce1}
.slide{position:relative;width:${SLIDE_W_PX}px;height:${SLIDE_H_PX}px;overflow:hidden;margin:0 0 24px;
  display:flex;flex-direction:column;padding:40px 64px 0;background:var(--background);color:var(--text);
  font-family:var(--font-body);font-size:16px;line-height:22px}
.tone-positive{color:var(--positive)} .tone-negative{color:var(--negative)}
.al-left{text-align:left} .al-right{text-align:right} .al-center{text-align:center}
.b{font-weight:700}

.hdr{display:flex;justify-content:space-between;align-items:flex-start;gap:40px;flex:none;margin-bottom:24px}
.hdr-text{flex:1;min-width:0}
.title{font-family:var(--font-heading);font-weight:700;font-size:32px;line-height:40px;color:var(--primary);
  max-height:80px;overflow:hidden}
.subtitle{margin-top:6px;font-size:16px;line-height:22px;color:var(--muted);white-space:nowrap;overflow:hidden}
.logo{flex:none;height:40px;width:120px;object-fit:contain}

.body{flex:1;min-height:0;display:flex;flex-direction:column}

.ftr{position:relative;flex:none;height:52px;display:flex;align-items:center;justify-content:space-between}
.ftr-rule{position:absolute;left:0;right:0;top:0;height:1px;background:var(--grid)}
.ftr-left,.ftr-right{font-size:12px;line-height:16px;color:var(--muted);white-space:nowrap}

/* key_takeaways */
.kpis{display:flex;gap:20px;flex:none;height:168px}
.kpi{flex:1;min-width:0;display:flex;flex-direction:column;padding:18px 20px;border-radius:10px;background:var(--surface)}
.kpi-mark{width:28px;height:4px;border-radius:2px;background:var(--accent);margin-bottom:14px;flex:none}
.kpi-value{font-family:var(--font-heading);font-weight:700;font-size:34px;line-height:40px;white-space:nowrap;overflow:hidden}
.kpi-unit{font-family:var(--font-body);font-weight:400;font-size:16px;color:var(--muted)}
.kpi-label{margin-top:4px;font-size:14px;line-height:18px;color:var(--muted);max-height:36px;overflow:hidden}
.kpi-delta{margin-top:auto;font-size:14px;line-height:18px;font-weight:700;white-space:nowrap;overflow:hidden}
.kpi-delta.tone-neutral{color:var(--muted)}
.takeaways{list-style:none;flex:1;display:flex;flex-direction:column;justify-content:center;gap:20px;padding-bottom:12px}
.tk{position:relative;min-height:30px}
.tk-num,.tk-num-text{position:absolute;left:0;top:-2px;width:30px;height:30px}
.tk-num{border-radius:50%;background:var(--primary)}
.tk-num-text{display:flex;align-items:center;justify-content:center;text-align:center;color:#fff;font-weight:700;font-size:15px;line-height:18px}
.tk-text{margin-left:48px;font-size:19px;line-height:27px;max-height:54px;overflow:hidden}

/* chart_table */
.ct-main{flex:1;min-height:0;display:flex;gap:36px}
.ct-chart-col{flex:1.4;min-width:0;display:flex;flex-direction:column}
.caption{font-size:13px;line-height:18px;color:var(--muted);margin-bottom:6px}
.chart{flex:1;min-height:0}
.chart svg{display:block}
.ct-table-col{flex:1;min-width:0}
.tbl{width:100%;border-collapse:collapse;table-layout:fixed;font-size:14px;line-height:18px}
.tbl th{padding:8px 10px;font-size:12px;line-height:16px;font-weight:700;color:var(--muted);border-bottom:2px solid var(--primary)}
.tbl td{padding:9px 10px;border-bottom:1px solid var(--grid)}
.tbl tr.total td{font-weight:700;background:var(--surface);border-bottom:none}
.callout{position:relative;flex:none;margin:16px 0 18px;padding:14px 20px 14px 32px;border-radius:8px;background:var(--surface)}
.callout-bar{position:absolute;left:14px;top:14px;bottom:14px;width:4px;border-radius:2px;background:var(--accent)}
.callout-text{font-size:16px;line-height:22px;max-height:44px;overflow:hidden}
`;

export function renderDocument(deck: DeckSpec, theme: Theme, logo?: string): string {
  const total = deck.slides.length;
  const slides = deck.slides.map(
    (slide, index) =>
      html`<section class="slide" data-slide="${index}" data-type="${slide.type}">
        ${renderSlide(slide, { theme, deck, logo, index, total })}
      </section>`,
  );
  // No scripts, no network: the page only ever shows inline CSS and data: images.
  return `<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'">
<style>${themeVars(theme)}${BASE_CSS}</style></head><body>${slides.map(String).join("")}</body></html>`;
}
