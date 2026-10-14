/**
 * Where to go after signing in. Only a path inside this app: "//evil.example" or
 * "/\\evil.example" would leave the site (protocol-relative), so they fall back to the list.
 */
export function safeNext(next: unknown): string {
  if (typeof next !== "string" || !next.startsWith("/")) return "/projects";
  if (next.startsWith("//") || next.startsWith("/\\")) return "/projects";
  return next;
}
