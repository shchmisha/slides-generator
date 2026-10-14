import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchHealth } from "./health";

function mockFetch(status: number, body: unknown): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(body), { status })),
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("fetchHealth", () => {
  it("returns the body on 200", async () => {
    mockFetch(200, { status: "ok", database: "ok", redis: "ok" });
    await expect(fetchHealth()).resolves.toEqual({ status: "ok", database: "ok", redis: "ok" });
  });

  it("returns the body on 503: it says what is down", async () => {
    mockFetch(503, { status: "fail", database: "fail", redis: "ok" });
    await expect(fetchHealth()).resolves.toEqual({ status: "fail", database: "fail", redis: "ok" });
  });

  it("throws ApiError with the server detail on other errors", async () => {
    mockFetch(500, { detail: "Внутренняя ошибка сервера" });
    await expect(fetchHealth()).rejects.toThrow("Внутренняя ошибка сервера");
  });
});
