/** A resolved video scenario, as the backend sends it (backend/app/jobs/render.py). */

export type VisualType =
  | "title"
  | "statement"
  | "bullets"
  | "big_number"
  | "comparison"
  | "steps"
  | "closing";

export interface Visual {
  type: VisualType;
  heading?: string;
  items?: string[];
  number?: string;
  caption?: string;
}

export interface Scene {
  start: number; // seconds from the beginning of the video
  duration: number;
  narration: string; // shown as captions (and voiced in a later version)
  visual: Visual;
}

export interface VideoSpec {
  title: string;
  scenes: Scene[];
}

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
  name?: string;
  colors: Record<ColorToken, string>;
  fonts: { heading: string; body: string };
}
