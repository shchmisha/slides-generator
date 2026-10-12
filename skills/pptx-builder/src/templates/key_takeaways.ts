import { html, type Raw } from "../html.ts";
import type { KeyTakeawaysSlide, RenderContext } from "../types.ts";
import { footer, header } from "./chrome.ts";

/** Opening slide: 3–4 KPI cards and 3–4 numbered conclusions. */
export function renderKeyTakeaways(slide: KeyTakeawaysSlide, ctx: RenderContext): Raw {
  return html`
    ${header(slide.title, slide.subtitle, ctx)}
    <div class="body kt">
      <div class="kpis">
        ${slide.kpis.map(
          (kpi) => html`<div class="kpi" data-pptx="shape">
            <div class="kpi-mark" data-pptx="shape"></div>
            <p class="kpi-value" data-pptx="text" data-fit="shrink">
              ${kpi.value}${kpi.unit ? html`<span class="kpi-unit"> ${kpi.unit}</span>` : ""}
            </p>
            <p class="kpi-label" data-pptx="text" data-fit="shrink">${kpi.label}</p>
            ${kpi.delta
              ? html`<p class="kpi-delta tone-${kpi.tone ?? "neutral"}" data-pptx="text">
                  ${kpi.delta}
                </p>`
              : ""}
          </div>`,
        )}
      </div>
      <ol class="takeaways">
        ${slide.takeaways.map(
          (text, i) => html`<li class="tk">
            <div class="tk-num" data-pptx="shape"></div>
            <p class="tk-num-text" data-pptx="text" data-valign="middle">${i + 1}</p>
            <p class="tk-text" data-pptx="text" data-fit="shrink">${text}</p>
          </li>`,
        )}
      </ol>
    </div>
    ${footer(ctx)}
  `;
}
