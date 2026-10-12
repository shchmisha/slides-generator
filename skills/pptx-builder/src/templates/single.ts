import { html, type Raw } from "../html.ts";
import type { ChartSlide, RenderContext, TableSlide } from "../types.ts";
import { footer, header } from "./chrome.ts";
import { callout, chart, table } from "./parts.ts";

/** One wide table: operational metrics by region, a full plan/fact breakdown. */
export function renderTable(slide: TableSlide, ctx: RenderContext): Raw {
  return html`
    ${header(slide.title, slide.subtitle, ctx)}
    <div class="body tb">
      <div class="tb-main">${table(slide.table, "tbl-wide")}</div>
      ${callout(slide.callout)}
    </div>
    ${footer(ctx)}
  `;
}

/** One wide chart: dynamics over months, a comparison across many categories. */
export function renderChart(slide: ChartSlide, ctx: RenderContext): Raw {
  return html`
    ${header(slide.title, slide.subtitle, ctx)}
    <div class="body ch">
      <div class="ch-main">${chart(slide.chart)}</div>
      ${callout(slide.callout)}
    </div>
    ${footer(ctx)}
  `;
}
