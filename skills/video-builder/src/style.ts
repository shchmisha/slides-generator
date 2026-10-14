/**
 * Video colours from the brand theme. A video reads best on a deep background, so the brand's
 * primary colour becomes the background and text is white or the brand text colour, whichever
 * has more contrast. Fonts get the same metric-compatible stand-ins as the slides.
 */

import substitutes from "../../pptx-builder/fonts/substitutes.json" with { type: "json" };

import type { Theme } from "./types.ts";

const HEX = /^#[0-9a-fA-F]{6}$/;
// Letters (any script), digits, spaces and hyphens: font names go into CSS as they are.
const FONT = /^[\p{L}\p{N} -]{1,64}$/u;

function luminance(hex: string): number {
  const channel = (i: number) => {
    const s = parseInt(hex.slice(i, i + 2), 16) / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5);
}

export function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

export interface Palette {
  background: string;
  ink: string; // headings and body text
  accent: string; // numbers, markers, the progress line
  soft: string; // secondary text (with transparency over the background)
  headingFont: string;
  bodyFont: string;
}

const SUBS = substitutes as unknown as Record<string, string[]>;

function stack(family: string, generic: "serif" | "sans-serif"): string {
  if (!FONT.test(family)) return generic;
  const names = [family, ...(SUBS[family.toLowerCase()] ?? [])];
  return [...names.map((n) => `"${n}"`), generic].join(",");
}

export function palette(theme: Theme): Palette {
  const pick = (value: string | undefined, fallback: string) =>
    value && HEX.test(value) ? value.toUpperCase() : fallback;
  const background = pick(theme.colors.primary, "#1F3A5F");
  const text = pick(theme.colors.text, "#1D1D1F");
  const ink = contrast("#FFFFFF", background) >= contrast(text, background) ? "#FFFFFF" : text;
  const accentRaw = pick(theme.colors.accent, "#E8A33D");
  const accent = contrast(accentRaw, background) >= 2.5 ? accentRaw : ink;
  return {
    background,
    ink,
    accent,
    soft: ink === "#FFFFFF" ? "rgba(255,255,255,0.78)" : "rgba(0,0,0,0.68)",
    headingFont: stack(theme.fonts.heading, "serif"),
    bodyFont: stack(theme.fonts.body, "sans-serif"),
  };
}
