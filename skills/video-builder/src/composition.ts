/**
 * Resolved video scenario + brand theme → a HyperFrames composition (index.html).
 *
 * Our templates, not the model, write all HTML: scenario texts are only ever escaped text
 * inside fixed markup. Every scene is a timed clip (`class="clip"` + data-start/duration),
 * captions are clips on their own track, and all motion is one paused GSAP timeline registered
 * on window.__timelines, which HyperFrames seeks frame by frame (no wall clock, no randomness).
 */

import { palette, type Palette } from "./style.ts";
import { captions, escapeHtml, typography } from "./text.ts";
import type { Scene, Theme, VideoSpec } from "./types.ts";

export const WIDTH = 1920;
export const HEIGHT = 1080;
export const FPS = 30;

const t = (text: string | undefined) => escapeHtml(typography(text ?? ""));
const sec = (x: number) => Math.round(x * 1000) / 1000;

function headingClass(text: string | undefined, big = true): string {
  const n = (text ?? "").length;
  if (big && n <= 26) return "h h-xl";
  return n <= 50 ? "h h-l" : "h h-m";
}

function heading(text: string | undefined, big = true): string {
  return text ? `<h1 class="${headingClass(text, big)}">${t(text)}</h1>` : "";
}

function caption(text: string | undefined): string {
  return text ? `<p class="cap">${t(text)}</p>` : "";
}

function items(list: string[] | undefined): string {
  return (list ?? []).map((item) => `<li class="item"><span class="dot"></span><span>${t(item)}</span></li>`).join("");
}

/** The markup of one scene's picture. */
export function sceneBody(scene: Scene): string {
  const v = scene.visual;
  switch (v.type) {
    case "title":
      return `<div class="bar"></div>${heading(v.heading)}${caption(v.caption)}`;
    case "statement":
      return `<div class="bar"></div>${heading(v.heading, false)}${caption(v.caption)}`;
    case "bullets":
      return `${heading(v.heading, false)}<ul class="items">${items(v.items)}</ul>`;
    case "big_number":
      return `<div class="number">${t(v.number)}</div>${caption(v.caption)}`;
    case "comparison":
      return `${heading(v.heading, false)}<div class="compare">${(v.items ?? [])
        .map((item) => `<div class="card">${t(item)}</div>`)
        .join("")}</div>`;
    case "steps":
      return `${heading(v.heading, false)}<ol class="steps">${(v.items ?? [])
        .map(
          (item, i) =>
            `<li class="step"><span class="step-num">${i + 1}</span><span class="step-text">${t(item)}</span></li>`,
        )
        .join("")}</ol>`;
    case "closing":
      return `<div class="bar"></div>${heading(v.heading)}${caption(v.caption)}`;
  }
}

const REVEAL_START = 0.7; // when a scene's points begin to appear
const REVEAL_TWEEN = 0.5;

/** Seconds between two points appearing: spread over the first half of the scene. */
function stagger(scene: Scene): number {
  const count = scene.visual.items?.length ?? 0;
  return count ? sec(Math.min(1.2, Math.max(0.3, (scene.duration * 0.5) / count))) : 0;
}

/** When everything in the scene is on screen: a good moment for its preview picture. */
export function settledAt(scene: Scene): number {
  const count = scene.visual.items?.length ?? 0;
  const reveals = count ? REVEAL_START + stagger(scene) * (count - 1) + REVEAL_TWEEN : 1.4;
  return sec(scene.start + Math.min(scene.duration - 0.6, reveals + 0.3));
}

/** GSAP calls for one scene, at absolute times, only for the elements it has. Selectors are
 * fixed: no scenario data ever reaches a script. */
function sceneMotion(scene: Scene, index: number, last: boolean): string[] {
  const s = `#s${index}`;
  const v = scene.visual;
  const t0 = scene.start;
  const lines = [`tl.from("${s}", { opacity: 0, duration: 0.4 }, ${sec(t0)});`];
  if (v.type === "title" || v.type === "statement" || v.type === "closing") {
    lines.push(`tl.from("${s} .bar", { scaleX: 0, duration: 0.6, ease: "power2.out" }, ${sec(t0 + 0.1)});`);
  }
  if (v.heading) {
    lines.push(`tl.from("${s} .h", { opacity: 0, y: 40, duration: 0.7, ease: "power2.out" }, ${sec(t0 + 0.15)});`);
  }
  if (v.caption && v.type !== "bullets" && v.type !== "steps" && v.type !== "comparison") {
    lines.push(`tl.from("${s} .cap", { opacity: 0, y: 20, duration: 0.6, ease: "power2.out" }, ${sec(t0 + 0.6)});`);
  }
  if (v.type === "big_number") {
    lines.push(
      `tl.from("${s} .number", { opacity: 0, scale: 0.6, duration: 0.8, ease: "back.out(1.6)" }, ${sec(t0 + 0.2)});`,
    );
  }
  const count = v.items?.length ?? 0;
  const parts = { bullets: [".item", "x: -40"], steps: [".step", "y: 40"], comparison: [".card", "y: 40"] } as const;
  if (count && (v.type === "bullets" || v.type === "steps" || v.type === "comparison")) {
    // Reveal the points over the first half of the scene, as the narrator gets to them.
    const [selector, offset] = parts[v.type];
    lines.push(
      `tl.from("${s} ${selector}", { opacity: 0, ${offset}, duration: ${REVEAL_TWEEN}, stagger: ${stagger(scene)}, ease: "power2.out" }, ${sec(t0 + REVEAL_START)});`,
    );
  }
  if (!last) lines.push(`tl.to("${s}", { opacity: 0, duration: 0.35 }, ${sec(t0 + scene.duration - 0.35)});`);
  return lines;
}

function css(p: Palette, hasLogo: boolean): string {
  const line = p.ink === "#FFFFFF" ? "rgba(255,255,255,0.22)" : "rgba(0,0,0,0.16)";
  return `
html, body { margin: 0; background: ${p.background}; }
#root { position: relative; width: ${WIDTH}px; height: ${HEIGHT}px; overflow: hidden; color: ${p.ink};
  font-family: ${p.bodyFont};
  background: radial-gradient(circle at 85% 0%, rgba(255,255,255,0.10), rgba(255,255,255,0) 55%), ${p.background}; }
.logo { position: absolute; top: 64px; right: 96px; height: 72px; max-width: 360px; object-fit: contain; }
.progress { position: absolute; left: 0; right: 0; bottom: 0; height: 8px; background: ${line}; }
.progress-fill { height: 100%; background: ${p.accent}; transform-origin: left center; }
.scene { position: absolute; left: 140px; right: ${hasLogo ? 140 : 140}px; top: 160px; height: 640px;
  display: flex; flex-direction: column; justify-content: center; }
.bar { width: 160px; height: 10px; border-radius: 5px; background: ${p.accent}; margin-bottom: 44px;
  transform-origin: left center; }
.h { font-family: ${p.headingFont}; font-weight: 700; line-height: 1.12; margin: 0; max-width: 1560px; }
.h-xl { font-size: 116px; } .h-l { font-size: 84px; } .h-m { font-size: 66px; }
.cap { font-size: 46px; line-height: 1.3; color: ${p.soft}; margin: 32px 0 0; max-width: 1500px; }
.items { list-style: none; padding: 0; margin: 48px 0 0; }
.item { display: flex; align-items: baseline; gap: 30px; font-size: 54px; line-height: 1.25; margin-bottom: 28px; }
.dot { flex: none; width: 20px; height: 20px; border-radius: 50%; background: ${p.accent}; transform: translateY(-8px); }
.number { font-family: ${p.headingFont}; font-weight: 700; font-size: 220px; line-height: 1.02; color: ${p.accent}; }
.steps { list-style: none; padding: 0; margin: 60px 0 0; display: flex; gap: 48px; }
.step { flex: 1; display: flex; flex-direction: column; gap: 28px; }
.step-num { width: 104px; height: 104px; border-radius: 50%; background: ${p.accent}; color: ${p.background};
  font-size: 56px; font-weight: 700; display: flex; align-items: center; justify-content: center; }
.step-text { font-size: 46px; line-height: 1.25; }
.compare { display: grid; grid-template-columns: repeat(2, 1fr); gap: 44px; margin-top: 60px; }
.card { border: 3px solid ${line}; border-radius: 28px; padding: 48px; font-size: 52px; line-height: 1.25; }
.card:first-child { border-color: ${p.accent}; }
.caption { position: absolute; left: 160px; right: 160px; bottom: 60px; display: flex; justify-content: center; }
.caption span { background: rgba(0,0,0,0.5); color: #FFFFFF; font-size: 40px; line-height: 1.35;
  padding: 14px 30px; border-radius: 14px; text-align: center; }
`;
}

export interface Composition {
  html: string;
  duration: number;
}

export function buildComposition(spec: VideoSpec, theme: Theme, hasLogo: boolean): Composition {
  if (!spec.scenes.length) throw new Error("the video has no scenes");
  const p = palette(theme);
  const last = spec.scenes[spec.scenes.length - 1]!;
  const duration = sec(last.start + last.duration);

  const scenes = spec.scenes
    .map(
      (scene, i) =>
        `<section id="s${i}" class="clip scene" data-start="${sec(scene.start)}" data-duration="${sec(scene.duration)}" data-track-index="0">${sceneBody(scene)}</section>`,
    )
    .join("\n");
  const allCaptions = spec.scenes.flatMap((scene) => captions(scene.narration, scene.start, scene.duration));
  const captionClips = allCaptions
    .map(
      (c, i) =>
        `<div id="c${i}" class="clip caption" data-start="${c.start}" data-duration="${c.duration}" data-track-index="1"><span>${t(c.text)}</span></div>`,
    )
    .join("\n");
  // The progress line steps forward at each scene instead of gliding: frames between
  // animations stay identical, so HyperFrames can reuse them instead of capturing each one.
  const count = spec.scenes.length;
  const motion = [
    `tl.set(".progress-fill", { scaleX: 0 }, 0);`,
    ...spec.scenes.map(
      (scene, i) =>
        `tl.to(".progress-fill", { scaleX: ${sec((i + 1) / count)}, duration: 0.6, ease: "power2.inOut" }, ${sec(scene.start)});`,
    ),
    ...spec.scenes.flatMap((scene, i) => sceneMotion(scene, i, i === spec.scenes.length - 1)),
    ...allCaptions.map((c, i) => `tl.from("#c${i}", { opacity: 0, duration: 0.2 }, ${c.start});`),
  ];

  const html = `<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=${WIDTH}, height=${HEIGHT}">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; media-src 'self'">
<title>${t(spec.title)}</title>
<script src="assets/gsap.min.js"></script>
<style>${css(p, hasLogo)}</style>
</head>
<body>
<div id="root" data-composition-id="root" data-start="0" data-duration="${duration}" data-width="${WIDTH}" data-height="${HEIGHT}">
${hasLogo ? '<img class="logo" src="assets/logo.png" alt="">' : ""}
<div class="progress"><div class="progress-fill"></div></div>
${scenes}
${captionClips}
</div>
<script>
window.__timelines = window.__timelines || {};
const tl = gsap.timeline({ paused: true });
${motion.join("\n")}
window.__timelines["root"] = tl;
</script>
</body>
</html>
`;
  return { html, duration };
}
