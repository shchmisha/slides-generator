import type { Raw } from "../html.ts";
import type { RenderContext, SlideSpec } from "../types.ts";
import { renderChartTable } from "./chart_table.ts";
import { renderCostStructure } from "./cost_structure.ts";
import { renderKeyTakeaways } from "./key_takeaways.ts";
import { renderRisksDecisions } from "./risks_decisions.ts";
import { renderChart, renderTable } from "./single.ts";

/** Slide-type registry. A new slide type = one template module + one entry here. */
export function renderSlide(slide: SlideSpec, ctx: RenderContext): Raw {
  switch (slide.type) {
    case "key_takeaways":
      return renderKeyTakeaways(slide, ctx);
    case "chart_table":
      return renderChartTable(slide, ctx);
    case "table":
      return renderTable(slide, ctx);
    case "chart":
      return renderChart(slide, ctx);
    case "cost_structure":
      return renderCostStructure(slide, ctx);
    case "risks_decisions":
      return renderRisksDecisions(slide, ctx);
  }
}
