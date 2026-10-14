/** Numbers, sizes, dates and durations for people. */

const dateFormat = new Intl.DateTimeFormat("ru-RU", {
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
});

export function when(iso: string | null | undefined): string {
  return iso ? dateFormat.format(new Date(iso)) : "—";
}

export function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} Б`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} КБ`;
  return `${(bytes / 1024 / 1024).toFixed(1).replace(".", ",")} МБ`;
}

export function seconds(ms: number | undefined | null): string {
  if (ms === undefined || ms === null) return "—";
  if (ms < 1000) return `${Math.round(ms)} мс`;
  const s = ms / 1000;
  return s < 60
    ? `${s.toFixed(1).replace(".", ",")} с`
    : `${Math.floor(s / 60)} мин ${Math.round(s % 60)} с`;
}

export function elapsed(fromIso: string | null, toIso?: string | null): number | null {
  if (!fromIso) return null;
  const end = toIso ? new Date(toIso).getTime() : Date.now();
  return Math.max(0, end - new Date(fromIso).getTime());
}
