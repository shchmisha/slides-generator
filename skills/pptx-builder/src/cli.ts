/**
 * layout: render-ready deck spec (JSON) → measured layout + slide previews.
 *
 *   node src/cli.ts --spec deck.json --theme theme.json [--logo logo.png] --out out/
 *
 * Writes out/layout.json (position and style of every element, read from Chromium),
 * out/slide-NN.png and out/deck.html. The PPTX is written from layout.json by the Python
 * emitter (pptx_builder). Exit code 0 even when there are layout issues: they are listed in
 * layout.json for the agent to fix. Non-zero only for invalid input or a crashed renderer.
 */

import { mkdir, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { parseArgs } from "node:util";

import { renderDocument, SLIDE_H_PX, SLIDE_W_PX } from "./document.ts";
import { layoutDeck } from "./measure.ts";
import { parseTheme } from "./theme.ts";
import type { DeckSpec } from "./types.ts";

const LAYOUT_VERSION = 1;

const PNG_MAGIC = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
const MAX_LOGO_BYTES = 2 * 1024 * 1024;

async function loadLogo(path: string | undefined): Promise<string | undefined> {
  if (!path) return undefined;
  const bytes = await readFile(path);
  if (!bytes.subarray(0, 8).equals(PNG_MAGIC)) throw new Error("logo must be a PNG file");
  if (bytes.length > MAX_LOGO_BYTES) throw new Error("logo is larger than 2 MB");
  return `data:image/png;base64,${bytes.toString("base64")}`;
}

function parseDeck(value: unknown): DeckSpec {
  const deck = value as Partial<DeckSpec> | null;
  if (!deck || typeof deck.title !== "string" || !Array.isArray(deck.slides) || deck.slides.length === 0) {
    throw new Error("deck spec needs a title and at least one slide");
  }
  return deck as DeckSpec;
}

async function main(): Promise<void> {
  const { values } = parseArgs({
    options: {
      spec: { type: "string" },
      theme: { type: "string" },
      logo: { type: "string" },
      out: { type: "string" },
    },
  });
  if (!values.spec || !values.theme || !values.out) {
    throw new Error("usage: --spec deck.json --theme theme.json [--logo logo.png] --out dir");
  }
  const deck = parseDeck(JSON.parse(await readFile(values.spec, "utf8")));
  const theme = parseTheme(JSON.parse(await readFile(values.theme, "utf8")));
  const logo = await loadLogo(values.logo);
  await mkdir(values.out, { recursive: true });

  const timings: Record<string, number> = {};
  let t = performance.now();
  const documentHtml = renderDocument(deck, theme, logo);
  await writeFile(join(values.out, "deck.html"), documentHtml);
  timings.html_ms = performance.now() - t;

  t = performance.now();
  const previews = deck.slides.map((_, i) => `slide-${String(i + 1).padStart(2, "0")}.png`);
  const layout = await layoutDeck(documentHtml, {
    theme,
    previewPaths: previews.map((name) => join(values.out!, name)),
  });
  timings.layout_ms = performance.now() - t;

  const layoutFile = {
    version: LAYOUT_VERSION,
    title: deck.title,
    slideSize: { widthPx: SLIDE_W_PX, heightPx: SLIDE_H_PX },
    theme,
    slides: layout.slides,
    issues: layout.issues,
    previews,
    timings,
  };
  await writeFile(join(values.out, "layout.json"), JSON.stringify(layoutFile));
  process.stdout.write(`${JSON.stringify({ issues: layout.issues, previews, timings })}\n`);
}

main().catch((err: unknown) => {
  process.stderr.write(`layout failed: ${err instanceof Error ? err.message : String(err)}\n`);
  process.exit(1);
});
