/** Text for the composition: escaped for HTML, split into timed captions. */

import type { SceneVoice, VoiceAlignment } from "./types.ts";

const ESCAPES: Record<string, string> = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

export function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (ch) => ESCAPES[ch] ?? ch);
}

/** Non-breaking spaces where Russian typography wants them: "12 услуг", "1 200", "в ЭДО". */
export function typography(text: string): string {
  return text
    .replace(/(\d) (?=\d{3}\b)/g, "$1 ")
    .replace(/(\d) (?=[%\p{L}])/gu, "$1 ")
    .replace(/(^|\s)([\p{L}]{1,2}) /gu, "$1$2 ");
}

export interface Caption {
  start: number;
  duration: number;
  text: string;
}

const MAX_CAPTION_CHARS = 84; // two lines at the caption size

function chunks(narration: string): string[] {
  const sentences = narration
    .split(/(?<=[.!?…])\s+/)
    .map((s) => s.trim())
    .filter(Boolean);
  const out: string[] = [];
  for (const sentence of sentences) {
    if (sentence.length <= MAX_CAPTION_CHARS) {
      out.push(sentence);
      continue;
    }
    let line = "";
    for (const word of sentence.split(/\s+/)) {
      if (line && line.length + word.length + 1 > MAX_CAPTION_CHARS) {
        out.push(line);
        line = word;
      } else {
        line = line ? `${line} ${word}` : word;
      }
    }
    if (line) out.push(line);
  }
  return out;
}

const round = (x: number) => Math.round(x * 1000) / 1000;

/** Captions for one scene, each shown for a share of the time proportional to its length:
 * the whole scene without a voice, the voice clip with one. */
export function captions(narration: string, start: number, duration: number, voice?: SceneVoice): Caption[] {
  const parts = chunks(narration);
  if (!parts.length) return [];
  if (voice?.alignment) {
    const timed = alignedCaptions(parts, narration, start + voice.offset, voice.alignment);
    if (timed) return timed;
  }
  const lead = voice ? voice.offset : Math.min(0.3, duration / 10);
  const usable = voice ? voice.duration : duration - 2 * lead;
  const total = parts.reduce((sum, p) => sum + p.length, 0);
  let at = start + lead;
  return parts.map((text) => {
    const length = (usable * text.length) / total;
    const caption = { start: round(at), duration: round(length), text };
    at += length;
    return caption;
  });
}

const visible = (text: string) => text.replace(/\s+/g, "");

/** Captions that follow the voice: each appears as its first word is spoken and stays until the
 * next one (the last a little after its last word). Chunks keep the narration's characters in
 * order with spaces normalised, so they are matched to the timing by non-space characters.
 * Null when the timing does not cover this text. */
function alignedCaptions(
  parts: string[],
  narration: string,
  clipStart: number,
  alignment: VoiceAlignment,
): Caption[] | null {
  const spoken = alignment.chars
    .map((c, i) => ({ c, i }))
    .filter(({ c }) => !/^\s*$/.test(c));
  if (spoken.map(({ c }) => c).join("") !== visible(narration)) return null;
  const times: { from: number; to: number }[] = [];
  let at = 0;
  for (const part of parts) {
    const count = visible(part).length;
    const first = spoken[at]!.i;
    const last = spoken[at + count - 1]!.i;
    times.push({ from: alignment.starts[first]!, to: alignment.ends[last]! });
    at += count;
  }
  return parts.map((text, k) => {
    const from = clipStart + Math.max(0, times[k]!.from - 0.1);
    const next = times[k + 1];
    const until = next ? clipStart + Math.max(0, next.from - 0.1) : clipStart + times[k]!.to + 0.5;
    return { start: round(from), duration: round(Math.max(0.3, until - from)), text };
  });
}
