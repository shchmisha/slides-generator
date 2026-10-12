import type { Raw } from "../html.ts";
import type { RenderContext, SlideSpec } from "../types.ts";
import { renderChartTable } from "./chart_table.ts";
import { renderKeyTakeaways } from "./key_takeaways.ts";

/** Slide-type registry. A new slide type = one template module + one entry here. */
export function renderSlide(slide: SlideSpec, ctx: RenderContext): Raw {
  switch (slide.type) {
    case "key_takeaways":
      return renderKeyTakeaways(slide, ctx);
    case "chart_table":
      return renderChartTable(slide, ctx);
  }
}
