/**
 * Minimal auto-escaping HTML templating.
 *
 * Slide text comes from user documents and from the LLM, so it is untrusted: every value
 * interpolated into `html\`\`` is escaped unless it is already a `Raw` produced by this module.
 */

const ESCAPES: Record<string, string> = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

export function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (ch) => ESCAPES[ch] ?? ch);
}

export class Raw {
  readonly value: string;
  constructor(value: string) {
    this.value = value;
  }
  toString(): string {
    return this.value;
  }
}

/** Mark trusted markup (produced by our own templates) so it is not escaped again. */
export const raw = (value: string): Raw => new Raw(value);

const NBSP = "\u00A0";

/**
 * Russian typography for slide text: keeps "2 877", "12,6 %", "млн ₽", "— " and short
 * prepositions from breaking across lines by turning those spaces into no-break spaces.
 */
export function typography(text: string): string {
  return text
    .replace(/(\d) (?=\d{3}(?!\d))/g, `$1${NBSP}`)
    .replace(/(\d) (?=[%₽‰])/g, `$1${NBSP}`)
    .replace(/(\d) (?=(?:тыс|млн|млрд|шт|п\.п)\.?(?![\p{L}]))/gu, `$1${NBSP}`)
    .replace(/(тыс\.|млн|млрд) (?=[₽$€])/g, `$1${NBSP}`)
    .replace(/ (?=[—–] )/g, NBSP)
    // Short prepositions and conjunctions ("в", "и", "на") stay with the next word.
    .replace(/(?<=^|[\s(«])([\p{L}]{1,2}) /gu, `$1${NBSP}`);
}

function renderValue(value: unknown): string {
  if (value instanceof Raw) return value.value;
  if (Array.isArray(value)) return value.map(renderValue).join("");
  if (value === null || value === undefined || value === false) return "";
  return escapeHtml(typography(String(value)));
}

export function html(strings: TemplateStringsArray, ...values: unknown[]): Raw {
  let out = strings[0] ?? "";
  values.forEach((value, i) => {
    out += renderValue(value) + (strings[i + 1] ?? "");
  });
  return new Raw(out);
}

/** JSON for a data-* attribute (escaped as an attribute value). */
export function jsonAttr(value: unknown): string {
  return escapeHtml(JSON.stringify(value));
}
