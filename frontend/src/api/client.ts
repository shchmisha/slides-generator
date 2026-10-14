/**
 * Thin fetch wrapper: same-origin `/api`, JSON in and out, typed errors.
 *
 * Every state-changing request carries `X-Requested-With: fetch`: the API refuses unsafe
 * methods without it (a page on another site cannot add that header without CORS, and CORS is
 * closed), so this is the CSRF protection together with the SameSite session cookie.
 */

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    /** The list of problems for a rejected scenario (422 from PUT /scenario). */
    public readonly errors: string[] = [],
    public readonly retryAfter: number | null = null,
    /** The parsed response body: health checks answer 503 with useful content. */
    public readonly body: unknown = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

type Method = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

let onUnauthorized: (() => void) | null = null;

/** Called on any 401 (the session ended): the app sends the user to the sign-in page. */
export function setUnauthorizedHandler(handler: () => void): void {
  onUnauthorized = handler;
}

function messageOf(status: number, body: unknown): string {
  if (body && typeof body === "object" && "detail" in body && typeof body.detail === "string") {
    return body.detail;
  }
  if (status === 413) return "Файл или запрос слишком большой";
  if (status === 429) return "Слишком много запросов. Попробуйте чуть позже";
  if (status >= 500) return "Сервис временно недоступен";
  return `Ошибка ${status}`;
}

function errorsOf(body: unknown): string[] {
  if (body && typeof body === "object" && "errors" in body && Array.isArray(body.errors)) {
    return body.errors.filter((e): e is string => typeof e === "string");
  }
  return [];
}

export async function request<T>(method: Method, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  let payload: BodyInit | undefined;
  if (method !== "GET") headers["X-Requested-With"] = "fetch";
  if (body instanceof FormData) {
    payload = body; // the browser sets the multipart boundary
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  const resp = await fetch(`/api${path}`, {
    method,
    headers,
    body: payload,
    credentials: "same-origin",
  });
  if (resp.status === 204) return undefined as T;
  const data: unknown = await resp.json().catch(() => null);
  if (!resp.ok) {
    if (resp.status === 401 && onUnauthorized && !path.startsWith("/auth/")) onUnauthorized();
    const retry = Number(resp.headers.get("Retry-After"));
    throw new ApiError(
      resp.status,
      messageOf(resp.status, data),
      errorsOf(data),
      Number.isFinite(retry) && retry > 0 ? retry : null,
      data,
    );
  }
  return data as T;
}

export const apiGet = <T>(path: string) => request<T>("GET", path);
export const apiPost = <T>(path: string, body?: unknown) => request<T>("POST", path, body);
export const apiPut = <T>(path: string, body?: unknown) => request<T>("PUT", path, body);
export const apiPatch = <T>(path: string, body?: unknown) => request<T>("PATCH", path, body);
export const apiDelete = (path: string) => request<void>("DELETE", path);

/** A message for the user from any error. */
export function errorText(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof TypeError) return "Нет связи с сервером";
  return err instanceof Error ? err.message : "Что-то пошло не так";
}
