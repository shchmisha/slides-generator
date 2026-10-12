/** Thin fetch wrapper: same-origin `/api`, JSON in/out, typed errors. */

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function apiGet<T>(path: string): Promise<T> {
  const resp = await fetch(`/api${path}`, {
    headers: { Accept: "application/json" },
    credentials: "same-origin",
  });
  const body: unknown = await resp.json().catch(() => null);
  if (!resp.ok) {
    const detail =
      body && typeof body === "object" && "detail" in body && typeof body.detail === "string"
        ? body.detail
        : `Ошибка ${resp.status}`;
    throw new ApiError(resp.status, detail);
  }
  return body as T;
}
