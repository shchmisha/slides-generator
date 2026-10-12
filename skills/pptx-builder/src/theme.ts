import type { ColorToken, Theme } from "./types.ts";

export const COLOR_TOKENS: readonly ColorToken[] = [
  "primary",
  "accent",
  "text",
  "muted",
  "positive",
  "negative",
  "surface",
  "grid",
  "background",
];

const HEX_COLOR = /^#[0-9a-fA-F]{6}$/;
// Font family names go into CSS and into the PPTX as-is: letters, digits, spaces and hyphens only.
const FONT_NAME = /^[\p{L}\p{N} -]{1,64}$/u;

/**
 * Themes come from user-supplied brand files, and their values are written into a <style>
 * block, so anything that is not a plain hex color or font name is rejected here.
 */
export function parseTheme(value: unknown): Theme {
  if (typeof value !== "object" || value === null) throw new Error("theme must be an object");
  const raw = value as { name?: unknown; colors?: unknown; fonts?: unknown };
  const colors = (raw.colors ?? {}) as Record<string, unknown>;
  const fonts = (raw.fonts ?? {}) as Record<string, unknown>;

  const parsedColors = {} as Record<ColorToken, string>;
  for (const token of COLOR_TOKENS) {
    const color = colors[token];
    if (typeof color !== "string" || !HEX_COLOR.test(color)) {
      throw new Error(`theme.colors.${token} must be a #RRGGBB color`);
    }
    parsedColors[token] = color.toUpperCase();
  }
  for (const key of ["heading", "body"] as const) {
    const font = fonts[key];
    if (typeof font !== "string" || !FONT_NAME.test(font)) {
      throw new Error(`theme.fonts.${key} must be a plain font family name`);
    }
  }
  return {
    name: typeof raw.name === "string" ? raw.name : "custom",
    colors: parsedColors,
    fonts: { heading: fonts.heading as string, body: fonts.body as string },
  };
}
