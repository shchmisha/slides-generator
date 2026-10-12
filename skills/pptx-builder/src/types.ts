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
  /** null = no data for this category (drawn as a gap, never as zero). */
  values: (number | null)[];
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

/** Full-width table with an optional callout. */
export interface TableSlide {
  type: "table";
  title: string;
  subtitle?: string;
  table: TableSpec;
  callout?: string;
}

/** Full-width chart with an optional callout. */
export interface ChartSlide {
  type: "chart";
  title: string;
  subtitle?: string;
  chart: ChartSpec;
  callout?: string;
}

export interface CostItem {
  label: string;
  /** Formatted amount, e.g. "1 147". */
  value: string;
  /** Share of the total in percent; sets the segment width. */
  share: number;
  shareText: string;
  /** Formatted change over the period, e.g. "+56 % за квартал". */
  change?: string;
  changeTone?: Tone;
}

/** Diagram: what the total consists of (proportional bar) and how each part changed. */
export interface CostStructureSlide {
  type: "cost_structure";
  title: string;
  subtitle?: string;
  total?: { label: string; value: string };
  items: CostItem[];
  callout?: string;
}

export interface Point {
  title: string;
  text: string;
}

/** Two columns: what worries us and what we propose. */
export interface RisksDecisionsSlide {
  type: "risks_decisions";
  title: string;
  subtitle?: string;
  risksTitle?: string;
  decisionsTitle?: string;
  risks: Point[];
  decisions: Point[];
}

export type SlideSpec =
  | KeyTakeawaysSlide
  | ChartTableSlide
  | TableSlide
  | ChartSlide
  | CostStructureSlide
  | RisksDecisionsSlide;

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
