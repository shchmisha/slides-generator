/** Text for the composition: escaped for HTML, split into timed captions. */

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

/** Captions for one scene, each shown for a share of the scene proportional to its length. */
export function captions(narration: string, start: number, duration: number): Caption[] {
  const parts = chunks(narration);
  if (!parts.length) return [];
  const lead = Math.min(0.3, duration / 10);
  const usable = duration - 2 * lead;
  const total = parts.reduce((sum, p) => sum + p.length, 0);
  const round = (x: number) => Math.round(x * 1000) / 1000;
  let at = start + lead;
  return parts.map((text) => {
    const length = (usable * text.length) / total;
    const caption = { start: round(at), duration: round(length), text };
    at += length;
    return caption;
  });
}
