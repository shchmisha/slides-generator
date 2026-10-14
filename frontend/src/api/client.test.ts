import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiDelete, apiGet, apiPost, setUnauthorizedHandler } from "./client";

function mockFetch(status: number, body: unknown, headers: Record<string, string> = {}) {
  const fn = vi.fn(
    async () =>
      new Response(status === 204 ? null : JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json", ...headers },
      }),
  );
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => {
  vi.unstubAllGlobals();
  setUnauthorizedHandler(() => undefined);
});

describe("request", () => {
  it("sends the CSRF header on unsafe methods only", async () => {
    const fetchMock = mockFetch(200, { ok: true });
    await apiGet("/projects");
    await apiPost("/projects", { title: "x" });
    const [getInit, postInit] = fetchMock.mock.calls.map((c) => (c as unknown[])[1] as RequestInit);
    expect((getInit?.headers as Record<string, string>)["X-Requested-With"]).toBeUndefined();
    expect((postInit?.headers as Record<string, string>)["X-Requested-With"]).toBe("fetch");
    expect(postInit?.body).toBe(JSON.stringify({ title: "x" }));
  });

  it("does not set a content type for file uploads", async () => {
    const fetchMock = mockFetch(201, {});
    const form = new FormData();
    form.append("file", new Blob(["x"]), "a.docx");
    await apiPost("/projects/1/files", form);
    const init = (fetchMock.mock.calls[0] as unknown[])[1] as RequestInit;
    expect((init.headers as Record<string, string>)["Content-Type"]).toBeUndefined();
    expect(init.body).toBe(form);
  });

  it("returns undefined for 204", async () => {
    mockFetch(204, null);
    await expect(apiDelete("/projects/1")).resolves.toBeUndefined();
  });

  it("carries the server's message, the list of problems and Retry-After", async () => {
    mockFetch(422, { detail: "Сценарий не прошёл проверку", errors: ["slides[0].title: …"] });
    const err = await apiPost("/x").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).message).toBe("Сценарий не прошёл проверку");
    expect((err as ApiError).errors).toEqual(["slides[0].title: …"]);

    mockFetch(429, null, { "Retry-After": "30" });
    const limited = (await apiPost("/x").catch((e: unknown) => e)) as ApiError;
    expect(limited.message).toMatch(/Слишком много запросов/);
    expect(limited.retryAfter).toBe(30);
  });

  it("reports an ended session except on the auth endpoints", async () => {
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    mockFetch(401, { detail: "Нужно войти" });
    await apiGet("/projects").catch(() => undefined);
    await apiPost("/auth/login", {}).catch(() => undefined);
    expect(handler).toHaveBeenCalledTimes(1);
  });
});
