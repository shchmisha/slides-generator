import { html, type Raw } from "../html.ts";
import type { ChartTableSlide, RenderContext } from "../types.ts";
import { footer, header } from "./chrome.ts";
import { callout, chart, table } from "./parts.ts";

/** Native chart on the left, compact table on the right, optional callout below. */
export function renderChartTable(slide: ChartTableSlide, ctx: RenderContext): Raw {
  return html`
    ${header(slide.title, slide.subtitle, ctx)}
    <div class="body ct">
      <div class="ct-main">
        <div class="ct-chart-col">${chart(slide.chart)}</div>
        <div class="ct-table-col">${table(slide.table)}</div>
      </div>
      ${callout(slide.callout)}
    </div>
    ${footer(ctx)}
  `;
}
