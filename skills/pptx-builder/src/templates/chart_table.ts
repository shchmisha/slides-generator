import { html, jsonAttr, raw, type Raw } from "../html.ts";
import type { ChartTableSlide, RenderContext, TableSpec } from "../types.ts";
import { footer, header } from "./chrome.ts";

function table(spec: TableSpec): Raw {
  const total = spec.columns.reduce((sum, c) => sum + (c.width ?? 1), 0);
  const lastRow = spec.rows.length - 1;
  return html`<table class="tbl" data-pptx="table">
    <colgroup>
      ${spec.columns.map((c) => raw(`<col style="width:${(((c.width ?? 1) / total) * 100).toFixed(2)}%" />`))}
    </colgroup>
    <thead>
      <tr>
        ${spec.columns.map((c) => html`<th class="al-${c.align}">${c.header}</th>`)}
      </tr>
    </thead>
    <tbody>
      ${spec.rows.map(
        (row, r) => html`<tr class="${spec.totalRow && r === lastRow ? "total" : ""}">
          ${row.map(
            (cell, c) =>
              html`<td
                class="al-${spec.columns[c]?.align ?? "left"} tone-${cell.tone ?? "neutral"} ${cell.bold
                  ? "b"
                  : ""}"
              >
                ${cell.text}
              </td>`,
          )}
        </tr>`,
      )}
    </tbody>
  </table>`;
}

/** Native chart on the left, compact table on the right, optional callout below. */
export function renderChartTable(slide: ChartTableSlide, ctx: RenderContext): Raw {
  return html`
    ${header(slide.title, slide.subtitle, ctx)}
    <div class="body ct">
      <div class="ct-main">
        <div class="ct-chart-col">
          ${slide.chart.caption
            ? html`<p class="caption" data-pptx="text">${slide.chart.caption}</p>`
            : ""}
          <div class="chart" data-pptx="chart" data-chart="${raw(jsonAttr(slide.chart))}"></div>
        </div>
        <div class="ct-table-col">${table(slide.table)}</div>
      </div>
      ${slide.callout
        ? html`<div class="callout" data-pptx="shape">
            <div class="callout-bar" data-pptx="shape"></div>
            <p class="callout-text" data-pptx="text" data-fit="shrink">${slide.callout}</p>
          </div>`
        : ""}
    </div>
    ${footer(ctx)}
  `;
}
