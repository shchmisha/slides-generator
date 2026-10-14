import assert from "node:assert/strict";
import { test } from "node:test";

import { audioClips, buildComposition, sceneBody, settledAt } from "../src/composition.ts";
import { contrast, palette } from "../src/style.ts";
import { captions, escapeHtml } from "../src/text.ts";
import type { Scene, Theme, VideoSpec } from "../src/types.ts";

const THEME: Theme = {
  colors: {
    primary: "#1F3A5F",
    accent: "#E8A33D",
    text: "#1D1D1F",
    muted: "#56616F",
    positive: "#2F7D4F",
    negative: "#B3352B",
    surface: "#F4F5F7",
    grid: "#D9DEE4",
    background: "#FFFFFF",
  },
  fonts: { heading: "Georgia", body: "Arial" },
};

const scene = (start: number, duration: number, visual: Scene["visual"], narration = "Фраза."): Scene => ({
  start,
  duration,
  narration,
  visual,
});

const SPEC: VideoSpec = {
  title: "Суперсервис",
  scenes: [
    scene(0, 10, { type: "title", heading: "Суперсервис", caption: "ЭДО под ключ" }),
    scene(10, 12, { type: "bullets", heading: "Условия", items: ["Бесплатно", "Без звонков", "Быстро"] }),
    scene(22, 10, { type: "big_number", number: "12 услуг", caption: "в одном окне" }),
    scene(32, 11, { type: "steps", items: ["Подписать", "Отправить"] }),
    scene(43, 11, { type: "comparison", heading: "Было и стало", items: ["Бумага", "Электронно"] }),
    scene(54, 9, { type: "statement", heading: "Документы приходят сами" }),
    scene(63, 9, { type: "closing", heading: "Итог", caption: "Всё в одном месте" }),
  ],
};

test("timing follows the scenario: one clip per scene, total duration from the last scene", () => {
  const { html, duration } = buildComposition(SPEC, THEME, true);
  assert.equal(duration, 72);
  assert.match(html, /data-composition-id="root" data-start="0" data-duration="72"/);
  assert.match(html, /id="s2" class="clip scene" data-start="22" data-duration="10"/);
  assert.equal((html.match(/class="clip scene"/g) ?? []).length, 7);
  assert.match(html, /window\.__timelines\["root"\] = tl;/);
  assert.match(html, /gsap\.timeline\(\{ paused: true \}\)/);
  assert.match(html, /<img class="logo" src="assets\/logo.png"/);
});

test("every visual type has its own markup", () => {
  assert.match(sceneBody(SPEC.scenes[1]!), /<li class="item">.*Бесплатно/);
  assert.match(sceneBody(SPEC.scenes[2]!), /<div class="number">12 услуг<\/div>/);
  assert.match(sceneBody(SPEC.scenes[3]!), /<span class="step-num">2<\/span>/);
  assert.equal((sceneBody(SPEC.scenes[4]!).match(/class="card"/g) ?? []).length, 2);
});

test("scenario text is escaped and never reaches a script", () => {
  const evil: VideoSpec = {
    title: "</title><script>alert(1)</script>",
    scenes: [scene(0, 10, { type: "statement", heading: '<img src=x onerror="fetch(1)">' }, "</script><script>x()</script>")],
  };
  const { html } = buildComposition(evil, THEME, false);
  assert.ok(!html.includes("<img src=x"));
  assert.ok(!html.includes("<script>alert"));
  assert.ok(!html.includes("<script>x()"));
  assert.match(html, /&lt;img src=x onerror=&quot;fetch\(1\)&quot;&gt;/);
  const script = html.slice(html.lastIndexOf("<script>"));
  assert.ok(!script.includes("onerror") && !script.includes("x()"));
  assert.match(html, /Content-Security-Policy" content="default-src 'none'/);
});

test("captions split the narration and fit inside their scene", () => {
  const narration =
    "Суперсервис — это комплекс операций в ЭДО. Мы берём на себя работу с документами, а вы занимаетесь своим делом и не думаете о бумагах. Всё бесплатно.";
  const list = captions(narration, 10, 12);
  assert.ok(list.length >= 3);
  assert.ok(list.every((c) => c.text.length <= 84));
  assert.ok(list[0]!.start >= 10);
  const end = list.at(-1)!.start + list.at(-1)!.duration;
  assert.ok(end <= 22 + 1e-6, `captions end at ${end}`);
  assert.equal(list.map((c) => c.text).join(" "), narration);
});

test("brand colours: deep primary as background, readable text and accent", () => {
  const p = palette(THEME);
  assert.equal(p.background, "#1F3A5F");
  assert.equal(p.ink, "#FFFFFF");
  assert.equal(p.accent, "#E8A33D");
  assert.equal(p.headingFont, '"Georgia","Gelasio","DejaVu Serif",serif');
  // A pale brand colour gets dark text instead of white.
  const pale = palette({ ...THEME, colors: { ...THEME.colors, primary: "#F6E9A8" } });
  assert.equal(pale.ink, "#1D1D1F");
  assert.ok(contrast(pale.ink, pale.background) >= 4.5);
  // A font name that would break out of CSS falls back to the generic family.
  assert.equal(palette({ ...THEME, fonts: { heading: 'x";}', body: "Arial" } }).headingFont, "serif");
});

test("escapeHtml covers the five special characters", () => {
  assert.equal(escapeHtml(`<a href="x">'&'</a>`), "&lt;a href=&quot;x&quot;&gt;&#39;&amp;&#39;&lt;/a&gt;");
});

test("posters are taken once every point is on screen, before the scene fades out", () => {
  const bullets = SPEC.scenes[1]!; // three points over a 12 s scene
  const at = settledAt(bullets);
  assert.ok(at >= 10 + 0.7 + 2 * 1.2 + 0.5, `poster at ${at}`);
  assert.ok(at <= 10 + 12 - 0.6);
  assert.ok(settledAt(SPEC.scenes[0]!) < 3); // a title settles quickly
});

test("the progress line steps at each scene instead of moving every frame", () => {
  const { html } = buildComposition(SPEC, THEME, false);
  assert.equal((html.match(/tl\.to\("\.progress-fill"/g) ?? []).length, 7);
  assert.ok(!html.includes(`duration: 72, ease: "none"`));
});


/** Timing like ElevenLabs returns: every character of the text, spaces included. */
function alignment(text: string, perChar = 0.06) {
  const chars = [...text];
  return {
    chars,
    starts: chars.map((_, i) => Math.round(i * perChar * 1000) / 1000),
    ends: chars.map((_, i) => Math.round((i + 1) * perChar * 1000) / 1000),
  };
}

test("a voiced scene gets its clip, and captions follow the spoken words", () => {
  const narration = "Первая фраза короткая. Вторая фраза идёт следом.";
  const voiced: Scene = {
    ...scene(10, 8, { type: "statement", heading: "Тезис" }, narration),
    voice: { file: "voice-02.mp3", offset: 0.35, duration: 2.9, alignment: alignment(narration) },
  };
  const subs = captions(voiced.narration, voiced.start, voiced.duration, voiced.voice);
  assert.deepEqual(subs.map((c) => c.text), ["Первая фраза короткая.", "Вторая фраза идёт следом."]);
  // The second sentence starts at character 23: 23 * 0.06 = 1.38 s into the clip, shown 0.1 s early.
  assert.equal(subs[1]!.start, Math.round((10 + 0.35 + 1.38 - 0.1) * 1000) / 1000);
  assert.equal(Math.round((subs[0]!.start + subs[0]!.duration) * 1000) / 1000, subs[1]!.start); // no gap
  const clips = audioClips({ title: "x", scenes: [voiced] }, 18);
  assert.deepEqual(clips, [
    '<audio id="voice0" src="assets/audio/voice-02.mp3" data-start="10.35" data-duration="2.9" data-volume="1"></audio>',
  ]);
});

test("timing that does not match the text falls back to the clip's length", () => {
  const voiced: Scene = {
    ...scene(0, 6, { type: "statement" }, "Одна фраза. Другая фраза."),
    voice: { file: "voice-01.mp3", offset: 0.5, duration: 4, alignment: alignment("совсем другой текст") },
  };
  const subs = captions(voiced.narration, voiced.start, voiced.duration, voiced.voice);
  assert.equal(subs[0]!.start, 0.5);
  assert.equal(Math.round((subs[1]!.start + subs[1]!.duration) * 1000) / 1000, 4.5);
});

test("the bed loops under the whole video and a cue marks each scene change", () => {
  const spec: VideoSpec = {
    ...SPEC,
    sounds: { bed: { file: "bed.mp3", seconds: 20 }, transition: { file: "transition.mp3", seconds: 1 } },
  };
  const total = 72;
  const clips = audioClips(spec, total);
  const beds = clips.filter((c) => c.includes("bed.mp3"));
  assert.equal(beds.length, 4); // 0, 20, 40, 60
  assert.match(beds[0]!, /data-fade-in="1.5"/);
  assert.match(beds[3]!, /data-start="60" data-duration="12".*data-fade-out="2.5"/);
  assert.equal(clips.filter((c) => c.includes("transition.mp3")).length, SPEC.scenes.length - 1);
  const { html, audioFiles } = buildComposition(spec, THEME, false);
  assert.deepEqual(audioFiles.sort(), ["bed.mp3", "transition.mp3"]);
  assert.match(html, /<audio id="bed0" src="assets\/audio\/bed.mp3"/);
});

test("audio file names outside the fixed set are refused", () => {
  const bad: Scene = {
    ...scene(0, 6, { type: "statement" }),
    voice: { file: "../../etc/passwd", offset: 0.3, duration: 2 },
  };
  assert.throws(() => audioClips({ title: "x", scenes: [bad] }, 6), /unexpected audio file name/);
  const silent = buildComposition(SPEC, THEME, false);
  assert.deepEqual(silent.audioFiles, []);
  assert.doesNotMatch(silent.html, /<audio/);
});
