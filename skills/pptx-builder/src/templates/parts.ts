import { html, jsonAttr, raw, type Raw } from "../html.ts";
import type { ChartSpec, TableSpec } from "../types.ts";

/** Native table: every cell is measured and re-created as a PowerPoint table cell. */
export function table(spec: TableSpec, extraClass = ""): Raw {
  const total = spec.columns.reduce((sum, c) => sum + (c.width ?? 1), 0);
  const lastRow = spec.rows.length - 1;
  return html`<table class="tbl ${extraClass}" data-pptx="table">
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

/** Chart box: a native chart in the PPTX, an SVG of the same size in the preview. */
export function chart(spec: ChartSpec): Raw {
  return html`${spec.caption ? html`<p class="caption" data-pptx="text">${spec.caption}</p>` : ""}
    <div class="chart" data-pptx="chart" data-chart="${raw(jsonAttr(spec))}"></div>`;
}

export function callout(text: string | undefined): Raw | "" {
  if (!text) return "";
  return html`<div class="callout" data-pptx="shape">
    <div class="callout-bar" data-pptx="shape"></div>
    <p class="callout-text" data-pptx="text" data-fit="shrink">${text}</p>
  </div>`;
}
