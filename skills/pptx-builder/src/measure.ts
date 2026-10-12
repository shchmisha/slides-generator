/**
 * Layout engine: lets Chromium lay out the slide HTML, shrinks text that does not fit,
 * then measures every `data-pptx` element so it can be re-created as a native PowerPoint
 * object at exactly the same position.
 */

import { chromium, type Page } from "playwright-core";

import { chartScales, renderChartSvg, type ChartScales } from "./preview_chart.ts";
import type { ChartSpec, Theme } from "./types.ts";

export interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface Rgba {
  hex: string;
  alpha: number;
}

export interface Run {
  text: string;
  font: string;
  sizePx: number;
  bold: boolean;
  italic: boolean;
  color: Rgba;
}

export interface Para {
  runs: Run[];
  align: "left" | "right" | "center" | "justify";
  lineHeightPx: number;
  spaceAfterPx: number;
}

export interface TableCellMeasure {
  text: string;
  font: string;
  sizePx: number;
  bold: boolean;
  color: Rgba;
  fill: Rgba;
  align: "left" | "right" | "center";
  padPx: [number, number, number, number];
  borderTop: { px: number; color: Rgba };
  borderBottom: { px: number; color: Rgba };
}

export type Measured =
  | { kind: "text"; box: Box; paras: Para[]; valign: "top" | "middle" | "bottom" }
  | {
      kind: "shape";
      box: Box;
      fill: Rgba;
      radiusPx: number;
      ellipse: boolean;
      border: { px: number; color: Rgba } | null;
    }
  | { kind: "image"; box: Box; src: string }
  | { kind: "chart"; box: Box; spec: ChartSpec; scales?: ChartScales }
  | { kind: "table"; box: Box; colW: number[]; rowH: number[]; cells: TableCellMeasure[][] };

export interface Issue {
  slide: number;
  element: string;
  kind: "overflow" | "outside" | "shrunk";
  detail: string;
}

export interface SlideMeasure {
  index: number;
  type: string;
  elements: Measured[];
}

export interface LayoutResult {
  slides: SlideMeasure[];
  issues: Issue[];
}

/* ---------- functions below run inside the page (serialized by Playwright) ---------- */

function fitTextInPage(): Array<{ slide: number; element: string; from: number; to: number }> {
  const shrunk: Array<{ slide: number; element: string; from: number; to: number }> = [];
  for (const el of Array.from(document.querySelectorAll<HTMLElement>('[data-fit="shrink"]'))) {
    const cs = getComputedStyle(el);
    const start = parseFloat(cs.fontSize);
    const ratio = parseFloat(cs.lineHeight) / start;
    const min = Math.ceil(start * 0.75);
    const overflows = () =>
      el.scrollHeight > el.clientHeight + 1 || el.scrollWidth > el.clientWidth + 1;
    let size = start;
    while (overflows() && size > min) {
      size -= 1;
      el.style.fontSize = `${size}px`;
      el.style.lineHeight = `${Math.round(size * ratio)}px`;
    }
    if (size !== start) {
      const slide = el.closest<HTMLElement>("section.slide");
      shrunk.push({
        slide: Number(slide?.dataset.slide ?? -1),
        element: el.className,
        from: start,
        to: size,
      });
    }
  }
  return shrunk;
}

function chartBoxesInPage(): Array<{ slide: number; nth: number; w: number; h: number }> {
  return Array.from(document.querySelectorAll<HTMLElement>('[data-pptx="chart"]')).map(
    (el, nth) => {
      const r = el.getBoundingClientRect();
      const slide = el.closest<HTMLElement>("section.slide");
      return { slide: Number(slide?.dataset.slide ?? -1), nth, w: r.width, h: r.height };
    },
  );
}

function injectSvgsInPage(svgs: string[]): void {
  const charts = Array.from(document.querySelectorAll<HTMLElement>('[data-pptx="chart"]'));
  charts.forEach((el, i) => {
    el.innerHTML = svgs[i] ?? "";
  });
}

function collectInPage(): { slides: SlideMeasure[]; issues: Issue[] } {
  const rgba = (css: string): Rgba => {
    const m = css.match(/rgba?\(([^)]+)\)/);
    if (!m || !m[1]) return { hex: "000000", alpha: 0 };
    const parts = m[1].split(/[\s,/]+/).filter(Boolean).map(Number);
    const [r = 0, g = 0, b = 0, a = 1] = parts;
    const hex = [r, g, b].map((v) => Math.round(v).toString(16).padStart(2, "0")).join("");
    return { hex: hex.toUpperCase(), alpha: a };
  };
  const font = (cs: CSSStyleDeclaration) =>
    (cs.fontFamily.split(",")[0] ?? "").trim().replace(/^["']|["']$/g, "");
  const px = (v: string) => parseFloat(v) || 0;
  const label = (el: Element) => `${el.tagName.toLowerCase()}.${el.className || "?"}`;
  const issues: Issue[] = [];

  const contentBox = (el: HTMLElement, base: DOMRect): Box => {
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    const left = px(cs.paddingLeft) + px(cs.borderLeftWidth);
    const top = px(cs.paddingTop) + px(cs.borderTopWidth);
    const right = px(cs.paddingRight) + px(cs.borderRightWidth);
    const bottom = px(cs.paddingBottom) + px(cs.borderBottomWidth);
    return {
      x: r.left - base.left + left,
      y: r.top - base.top + top,
      w: r.width - left - right,
      h: r.height - top - bottom,
    };
  };
  const borderBox = (el: Element, base: DOMRect): Box => {
    const r = el.getBoundingClientRect();
    return { x: r.left - base.left, y: r.top - base.top, w: r.width, h: r.height };
  };

  const textParas = (el: HTMLElement): Para[] => {
    const blocks = Array.from(el.querySelectorAll<HTMLElement>(":scope > [data-para]"));
    const paraEls = blocks.length > 0 ? blocks : [el];
    return paraEls.map((p) => {
      const pcs = getComputedStyle(p);
      const runs: Run[] = [];
      const walker = document.createTreeWalker(p, NodeFilter.SHOW_TEXT);
      for (let n = walker.nextNode(); n; n = walker.nextNode()) {
        // Collapse only ASCII whitespace: keep no-break spaces inside numbers like "2 877".
        const text = (n.nodeValue ?? "").replace(/[ \t\n\r\f]+/g, " ");
        if (!text || !n.parentElement) continue;
        const cs = getComputedStyle(n.parentElement);
        runs.push({
          text,
          font: font(cs),
          sizePx: px(cs.fontSize),
          bold: Number(cs.fontWeight) >= 600,
          italic: cs.fontStyle === "italic",
          color: rgba(cs.color),
        });
      }
      // Trim like the browser does at paragraph edges.
      if (runs[0]) runs[0].text = runs[0].text.replace(/^ /, "");
      const last = runs[runs.length - 1];
      if (last) last.text = last.text.replace(/ $/, "");
      const align = (["left", "right", "center", "justify"] as const).find(
        (a) => a === pcs.textAlign,
      );
      return {
        runs: runs.filter((r) => r.text.length > 0),
        align: align ?? "left",
        lineHeightPx: px(pcs.lineHeight) || px(pcs.fontSize) * 1.2,
        spaceAfterPx: px(pcs.marginBottom),
      };
    });
  };

  const slides: SlideMeasure[] = [];
  for (const section of Array.from(document.querySelectorAll<HTMLElement>("section.slide"))) {
    const index = Number(section.dataset.slide);
    const base = section.getBoundingClientRect();
    const elements: Measured[] = [];
    const body = section.querySelector(".body")?.getBoundingClientRect();

    for (const el of Array.from(section.querySelectorAll<HTMLElement>("[data-pptx]"))) {
      const kind = el.dataset.pptx;
      const cs = getComputedStyle(el);
      const outer = el.getBoundingClientRect();

      if (kind !== "shape" && (el.scrollHeight > el.clientHeight + 1 || el.scrollWidth > el.clientWidth + 1)) {
        issues.push({
          slide: index,
          element: label(el),
          kind: "overflow",
          detail: `content ${el.scrollWidth}×${el.scrollHeight}px > box ${el.clientWidth}×${el.clientHeight}px`,
        });
      }
      if (
        outer.right > base.right + 1 ||
        outer.bottom > base.bottom + 1 ||
        (body && el.closest(".body") && outer.bottom > body.bottom + 1)
      ) {
        issues.push({ slide: index, element: label(el), kind: "outside", detail: "element leaves its area" });
      }

      if (kind === "text") {
        const v = el.dataset.valign;
        elements.push({
          kind: "text",
          box: contentBox(el, base),
          paras: textParas(el),
          valign: v === "middle" || v === "bottom" ? v : "top",
        });
      } else if (kind === "shape") {
        const fill = rgba(cs.backgroundColor);
        const bw = px(cs.borderTopWidth);
        const border = bw > 0 ? { px: bw, color: rgba(cs.borderTopColor) } : null;
        if (fill.alpha === 0 && !border) continue;
        const box = borderBox(el, base);
        const radiusCss = cs.borderTopLeftRadius;
        const radiusPx = radiusCss.endsWith("%")
          ? (Math.min(box.w, box.h) * parseFloat(radiusCss)) / 100
          : px(radiusCss);
        elements.push({
          kind: "shape",
          box,
          fill,
          radiusPx,
          ellipse: radiusPx >= Math.min(box.w, box.h) / 2 - 0.5 && Math.abs(box.w - box.h) < 1,
          border,
        });
      } else if (kind === "image" && el instanceof HTMLImageElement) {
        const cb = contentBox(el, base);
        const scale = Math.min(cb.w / el.naturalWidth, cb.h / el.naturalHeight);
        const w = el.naturalWidth * scale;
        const h = el.naturalHeight * scale;
        const [posX = "50%", posY = "50%"] = cs.objectPosition.split(" ");
        elements.push({
          kind: "image",
          box: {
            x: cb.x + ((cb.w - w) * parseFloat(posX)) / 100,
            y: cb.y + ((cb.h - h) * parseFloat(posY)) / 100,
            w,
            h,
          },
          src: el.src,
        });
      } else if (kind === "chart") {
        elements.push({ kind: "chart", box: contentBox(el, base), spec: JSON.parse(el.dataset.chart ?? "{}") });
      } else if (kind === "table" && el instanceof HTMLTableElement) {
        const rows = Array.from(el.rows);
        const cells: TableCellMeasure[][] = rows.map((tr) =>
          Array.from(tr.cells).map((td) => {
            const c = getComputedStyle(td);
            const align = (["left", "right", "center"] as const).find((a) => a === c.textAlign);
            return {
              text: (td.textContent ?? "").replace(/[ \t\n\r\f]+/g, " ").trim(),
              font: font(c),
              sizePx: px(c.fontSize),
              bold: Number(c.fontWeight) >= 600,
              color: rgba(c.color),
              fill: rgba(c.backgroundColor),
              align: align ?? "left",
              padPx: [px(c.paddingTop), px(c.paddingRight), px(c.paddingBottom), px(c.paddingLeft)],
              borderTop: { px: px(c.borderTopWidth), color: rgba(c.borderTopColor) },
              borderBottom: { px: px(c.borderBottomWidth), color: rgba(c.borderBottomColor) },
            };
          }),
        );
        const firstRow = rows[0];
        elements.push({
          kind: "table",
          box: borderBox(el, base),
          colW: firstRow ? Array.from(firstRow.cells).map((c) => c.getBoundingClientRect().width) : [],
          rowH: rows.map((r) => r.getBoundingClientRect().height),
          cells,
        });
      }
    }
    slides.push({ index, type: section.dataset.type ?? "?", elements });
  }
  return { slides, issues };
}

/* ------------------------------------- Node side ------------------------------------- */

export interface LayoutOptions {
  theme: Theme;
  /** Write one PNG per slide here (preview for the user and for visual QA). */
  previewPaths?: string[];
}

async function blockNetwork(page: Page): Promise<void> {
  // The page is self-contained; any request (fonts, trackers, internal hosts) is refused.
  await page.route("**/*", (route) => route.abort());
}

export async function layoutDeck(documentHtml: string, opts: LayoutOptions): Promise<LayoutResult> {
  const browser = await chromium.launch({
    executablePath: process.env.CHROMIUM_PATH || undefined,
    args: ["--disable-gpu", "--font-render-hinting=none"],
  });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
    await blockNetwork(page);
    await page.setContent(documentHtml, { waitUntil: "load" });
    await page.evaluate(() => document.fonts.ready.then(() => undefined));

    const shrunk = await page.evaluate(fitTextInPage);

    // Charts are native objects in PPTX; in the preview they are drawn as SVG of the same size.
    const boxes = await page.evaluate(chartBoxesInPage);
    const specs = await page.$$eval('[data-pptx="chart"]', (els) =>
      els.map((el) => (el as HTMLElement).dataset.chart ?? "{}"),
    );
    const svgs = boxes.map((b, i) =>
      renderChartSvg(JSON.parse(specs[i] ?? "{}") as ChartSpec, b.w, b.h, opts.theme),
    );
    await page.evaluate(injectSvgsInPage, svgs);

    const result = await page.evaluate(collectInPage);
    for (const slide of result.slides) {
      for (const el of slide.elements) {
        if (el.kind === "chart") el.scales = chartScales(el.spec);
      }
    }
    for (const s of shrunk) {
      result.issues.push({
        slide: s.slide,
        element: s.element,
        kind: "shrunk",
        detail: `font ${s.from}px → ${s.to}px to fit`,
      });
    }

    if (opts.previewPaths) {
      const sections = await page.$$("section.slide");
      await Promise.all(
        sections.map((section, i) => {
          const path = opts.previewPaths?.[i];
          return path ? section.screenshot({ path }) : Promise.resolve();
        }),
      );
    }
    return result;
  } finally {
    await browser.close();
  }
}
