import { html, raw, type Raw } from "../html.ts";
import type { CostStructureSlide, RenderContext, Theme } from "../types.ts";
import { footer, header } from "./chrome.ts";
import { callout } from "./parts.ts";

/** Segments narrower than this get no label inside; the list below names them all. */
const MIN_LABELLED_SHARE = 9;
const SHADES = [1, 0.8, 0.62, 0.46, 0.32, 0.22, 0.15];

function rgb(hex: string): [number, number, number] {
  const n = Number.parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

/** The primary color blended into the background: solid tints, no transparency needed. */
function shade(theme: Theme, i: number): { fill: string; dark: boolean } {
  const t = SHADES[Math.min(i, SHADES.length - 1)] ?? 0.15;
  const [r1, g1, b1] = rgb(theme.colors.primary);
  const [r2, g2, b2] = rgb(theme.colors.background);
  const mix = [r1 * t + r2 * (1 - t), g1 * t + g2 * (1 - t), b1 * t + b2 * (1 - t)].map(Math.round);
  const luminance = (0.2126 * mix[0]! + 0.7152 * mix[1]! + 0.0722 * mix[2]!) / 255;
  return { fill: `rgb(${mix.join(",")})`, dark: luminance < 0.55 };
}

/** Proportional bar of the parts of a total, then each part with its amount, share and change. */
export function renderCostStructure(slide: CostStructureSlide, ctx: RenderContext): Raw {
  const shades = slide.items.map((_, i) => shade(ctx.theme, i));
  return html`
    ${header(slide.title, slide.subtitle, ctx)}
    <div class="body cs">
      ${slide.total
        ? html`<p class="cs-total" data-pptx="text">
            ${slide.total.label}: <b class="cs-total-value">${slide.total.value}</b>${slide.changeLabel
              ? html`<span class="cs-change-label"> · ${slide.changeLabel}</span>`
              : ""}
          </p>`
        : ""}
      <div class="cs-bar">
        ${slide.items.map((item, i) => {
          const s = shades[i]!;
          return html`<div
            class="cs-seg"
            data-pptx="shape"
            style="${raw(`flex:${Math.max(item.share, 0.5).toFixed(3)} 1 0;background:${s.fill}`)}"
          >
            ${item.share >= MIN_LABELLED_SHARE
              ? html`<p class="cs-seg-text ${s.dark ? "on-dark" : ""}" data-pptx="text">${item.shareText}</p>`
              : ""}
          </div>`;
        })}
      </div>
      <div class="cs-list">
        ${slide.items.map(
          (item, i) => html`<div class="cs-item">
            <div class="cs-dot" data-pptx="shape" style="${raw(`background:${shades[i]!.fill}`)}"></div>
            <p class="cs-label" data-pptx="text" data-fit="shrink">${item.label}</p>
            <p class="cs-meta" data-pptx="text">${item.value} · ${item.shareText}</p>
            ${item.change
              ? html`<p class="cs-change tone-${item.changeTone ?? "neutral"}" data-pptx="text">${item.change}</p>`
              : ""}
          </div>`,
        )}
      </div>
      ${callout(slide.callout)}
    </div>
    ${footer(ctx)}
  `;
}
