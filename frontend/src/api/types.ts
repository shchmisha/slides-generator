/** Shapes of the API's answers (backend/app/api/*.py). */

export type ProjectKind = "deck" | "video";
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

export interface User {
  id: string;
  email: string;
}

/** Voice-over with the user's own ElevenLabs key; the key itself is never sent back. */
export interface VoiceStatus {
  enabled: boolean;
  voice_id: string | null;
  voice_name: string | null;
  updated_at: string | null;
  model: string;
}

export interface VoiceInfo {
  id: string;
  name: string;
  description: string;
}

export interface FileOut {
  id: string;
  filename: string;
  kind: "docx" | "xlsx" | "pptx" | "text";
  size: number;
  created_at: string;
  notes: string[];
}

export interface Theme {
  name: string;
  colors: Record<ColorToken, string>;
  fonts: { heading: string; body: string };
}

export interface DeckSettings {
  slides: number | null;
  language: "ru" | "en";
  tone: "business" | "neutral" | "friendly";
}

export interface VideoSettings {
  min_seconds: number;
  max_seconds: number;
  language: "ru" | "en";
  tone: "business" | "neutral" | "friendly";
}

export interface Project {
  id: string;
  title: string;
  kind: ProjectKind;
  settings: DeckSettings | VideoSettings;
  brand: Theme | null;
  has_logo: boolean;
  files: FileOut[];
  created_at: string;
  updated_at: string;
}

export type JobStatus = "queued" | "running" | "succeeded" | "failed";

export interface LayoutIssue {
  slide: number;
  element: string;
  kind: "shrunk" | "overflow" | "outside";
  detail: string;
}

export interface Job {
  id: string;
  project_id: string;
  type: "plan" | "render";
  status: JobStatus;
  progress: number;
  stage: string;
  error: string | null;
  scenario_version: number | null;
  timings: Record<string, number>;
  report: {
    attempts?: { latency_ms: number; errors: string[]; findings: string[] }[];
    issues?: LayoutIssue[];
    warnings?: string[];
    slides?: number;
    /** A recorded video: its scene count and length. */
    scenes?: number;
    seconds?: number;
    starts?: number[];
    errors?: string[];
  };
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export const isActive = (job: Job | null | undefined): boolean =>
  !!job && (job.status === "queued" || job.status === "running");

export interface Note {
  severity: "info" | "warning";
  source: string;
  location: string | null;
  message: string;
  kind: "data" | "quarantine";
}

export interface ClaimCheck {
  quote: string;
  label: string;
  value: string;
  matches: boolean | null;
}

/** A name the scenario can use in {placeholders}, with its figure. */
export interface ValueInfo {
  name: string;
  label: string;
  value?: string;
  signed?: string;
  items?: { key: string; value: string; signed: string }[];
}

/* ------------------------------------------------------------------ resolved slides (preview) */

export interface SpecTable {
  columns: { header: string; align: "left" | "right" | "center" }[];
  rows: { text: string; tone?: "positive" | "negative" | "neutral"; bold?: boolean }[][];
  totalRow?: boolean;
}

export interface SpecChart {
  caption?: string;
  categories: string[];
  series: { name: string; values: (number | null)[]; kind: "bar" | "line"; axis?: string }[];
}

export interface SpecSlide {
  type: string;
  title: string;
  subtitle?: string;
  callout?: string;
  kpis?: { label: string; value: string; unit?: string; delta?: string; tone?: string }[];
  takeaways?: string[];
  chart?: SpecChart;
  table?: SpecTable;
  total?: { label: string; value: string };
  items?: { label: string; value: string; shareText: string; change?: string }[];
  changeLabel?: string;
  risks?: { title: string; text: string }[];
  decisions?: { title: string; text: string }[];
}

export interface DeckReport {
  warnings: string[];
  notes: Note[];
  claims?: ClaimCheck[];
  slides?: number;
  preview?: { title: string; footer?: string; slides: SpecSlide[] };
  preview_origins?: (number | null)[];
  values?: ValueInfo[];
}

export interface VideoReport {
  warnings: string[];
  notes: Note[];
  seconds: number;
  scenes: number;
  min_seconds?: number;
  max_seconds?: number;
}

/* --------------------------------------------------------------- scenario (what users edit) */

export type Json = Record<string, unknown>;

export interface DeckContent extends Json {
  kind: "deck";
  title: string;
  footer?: string;
  slides: Json[];
}

export interface VideoScene extends Json {
  duration_s: number;
  narration: string;
  visual: {
    type: string;
    heading?: string;
    items?: string[];
    number?: string;
    caption?: string;
  };
}

export interface VideoContent extends Json {
  kind: "video";
  title: string;
  scenes: VideoScene[];
}

export interface ScenarioSummary {
  id: string;
  version: number;
  source: "model" | "user";
  created_at: string;
}

export interface Scenario extends ScenarioSummary {
  content: DeckContent | VideoContent;
  report: DeckReport | VideoReport;
}

export interface Artifact {
  id: string;
  job_id: string;
  kind: "pptx" | "mp4" | "preview";
  position: number;
  size: number;
  created_at: string;
  url: string;
}

export interface BrandOut {
  theme: Theme;
  warnings: string[];
}

/** The editable texts of a deck slide (structure and figures are edited in the JSON tab). */
export interface SlideContent {
  type: string;
  title?: string;
  subtitle?: string;
  callout?: string;
  total_label?: string;
  kpis?: { label: string; delta?: string }[];
  takeaways?: string[];
  chart?: { caption?: string; series?: { name: string }[] };
  table?: { row_header?: string; columns?: { header: string }[]; total_label?: string };
  risks?: { title: string; text: string }[];
  decisions?: { title: string; text: string }[];
}
