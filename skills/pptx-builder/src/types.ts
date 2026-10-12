/**
 * Render-ready deck specification.
 *
 * This is what the builder consumes: every number is already resolved from the source data
 * by the backend (the LLM only references datasets, it never types figures). Text values are
 * pre-formatted strings; chart values are raw numbers plus an Excel format code so that the
 * chart stays editable in PowerPoint.
 */

export type ColorToken =
  | "primary"
  | "accent"
  | "text"
  | "muted"
  | "positive"
  | "negative"
  | "surface"
  | "grid"
  | "background";

export interface Theme {
  name: string;
  colors: Record<ColorToken, string>;
  fonts: { heading: string; body: string };
}

export type Tone = "positive" | "negative" | "neutral";

export interface Kpi {
  label: string;
  value: string;
  unit?: string;
  delta?: string;
  tone?: Tone;
}

export interface KeyTakeawaysSlide {
  type: "key_takeaways";
  title: string;
  subtitle?: string;
  kpis: Kpi[];
  takeaways: string[];
}

export interface ChartSeries {
  name: string;
  values: number[];
  kind: "bar" | "line";
  axis?: "primary" | "secondary";
  color: ColorToken;
  showValues?: boolean;
}

export interface ChartSpec {
  caption?: string;
  categories: string[];
  series: ChartSeries[];
  /** Excel number format for the primary axis, e.g. `#,##0`. */
  valueFormat: string;
  secondaryFormat?: string;
  secondaryMin?: number;
  secondaryMax?: number;
}

export interface TableColumn {
  header: string;
  align: "left" | "right" | "center";
  /** Relative width (fr units). */
  width?: number;
}

export interface TableCell {
  text: string;
  tone?: Tone;
  bold?: boolean;
}

export interface TableSpec {
  columns: TableColumn[];
  rows: TableCell[][];
  /** Style the last row as a total. */
  totalRow?: boolean;
}

export interface ChartTableSlide {
  type: "chart_table";
  title: string;
  subtitle?: string;
  chart: ChartSpec;
  table: TableSpec;
  callout?: string;
}

export type SlideSpec = KeyTakeawaysSlide | ChartTableSlide;

export interface DeckSpec {
  title: string;
  footer?: string;
  slides: SlideSpec[];
}

/** Everything a slide template needs besides its own spec. */
export interface RenderContext {
  theme: Theme;
  deck: DeckSpec;
  /** `data:` URL of the logo, if any. */
  logo?: string;
  index: number;
  total: number;
}
