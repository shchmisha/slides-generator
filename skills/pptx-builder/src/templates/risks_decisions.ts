import { html, type Raw } from "../html.ts";
import type { Point, RenderContext, RisksDecisionsSlide } from "../types.ts";
import { footer, header } from "./chrome.ts";

function column(heading: string, points: Point[], kind: "risk" | "decision"): Raw {
  return html`<div class="rd-col">
    <p class="rd-head rd-head-${kind}" data-pptx="text">${heading}</p>
    ${points.map(
      (point) => html`<div class="rd-card" data-pptx="shape">
        <div class="rd-bar rd-bar-${kind}" data-pptx="shape"></div>
        <p class="rd-title" data-pptx="text" data-fit="shrink">${point.title}</p>
        <p class="rd-text" data-pptx="text" data-fit="shrink">${point.text}</p>
      </div>`,
    )}
  </div>`;
}

/** "Риски и решения": what worries us on the left, what we propose on the right. */
export function renderRisksDecisions(slide: RisksDecisionsSlide, ctx: RenderContext): Raw {
  return html`
    ${header(slide.title, slide.subtitle, ctx)}
    <div class="body rd">
      <div class="rd-main">
        ${column(slide.risksTitle ?? "Риски", slide.risks, "risk")}
        ${column(slide.decisionsTitle ?? "Решения", slide.decisions, "decision")}
      </div>
    </div>
    ${footer(ctx)}
  `;
}
