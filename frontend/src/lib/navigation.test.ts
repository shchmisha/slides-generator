import { describe, expect, it } from "vitest";

import { safeNext } from "./navigation";

describe("safeNext", () => {
  it("keeps paths inside the app and refuses anything that leaves it", () => {
    expect(safeNext("/projects/1/scenario")).toBe("/projects/1/scenario");
    for (const bad of [
      "//evil.example",
      "/\\evil.example",
      "https://evil.example",
      undefined,
      ["/x"],
    ]) {
      expect(safeNext(bad)).toBe("/projects");
    }
  });
});
