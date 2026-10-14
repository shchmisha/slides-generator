import { mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { defineComponent, h } from "vue";

import type { Job } from "../api/types";
import { POLL_MS, useJob } from "./useJob";

const get = vi.fn();
vi.mock("../api/endpoints", () => ({ jobs: { get: (id: string) => get(id) } }));

function job(status: Job["status"], progress = 0): Job {
  return {
    id: "j1",
    project_id: "p1",
    type: "plan",
    status,
    progress,
    stage: status,
    error: null,
    scenario_version: null,
    timings: {},
    report: {},
    created_at: "2026-10-13T10:00:00Z",
    started_at: null,
    finished_at: null,
  };
}

function harness(onFinished: (j: Job) => void) {
  let api!: ReturnType<typeof useJob>;
  const wrapper = mount(
    defineComponent({
      setup() {
        api = useJob(onFinished);
        return () => h("div");
      },
    }),
  );
  return { api, wrapper };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  vi.useRealTimers();
  get.mockReset();
});

describe("useJob", () => {
  it("polls until the job finishes, then reports it once", async () => {
    get.mockResolvedValueOnce(job("running", 40)).mockResolvedValueOnce(job("succeeded", 100));
    const finished = vi.fn();
    const { api } = harness(finished);
    api.follow(job("queued"));
    await vi.advanceTimersByTimeAsync(POLL_MS);
    expect(api.job.value?.progress).toBe(40);
    await vi.advanceTimersByTimeAsync(POLL_MS);
    expect(api.job.value?.status).toBe("succeeded");
    expect(finished).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(POLL_MS * 5);
    expect(get).toHaveBeenCalledTimes(2);
  });

  it("keeps polling through network errors, more slowly", async () => {
    get
      .mockRejectedValueOnce(new TypeError("offline"))
      .mockResolvedValueOnce(job("succeeded", 100));
    const { api } = harness(() => undefined);
    api.follow(job("running"));
    await vi.advanceTimersByTimeAsync(POLL_MS);
    expect(api.error.value).toBe("Нет связи с сервером");
    await vi.advanceTimersByTimeAsync(POLL_MS);
    expect(get).toHaveBeenCalledTimes(1); // backed off to twice the interval
    await vi.advanceTimersByTimeAsync(POLL_MS);
    expect(api.job.value?.status).toBe("succeeded");
    expect(api.error.value).toBeNull();
  });

  it("stops when the component goes away", async () => {
    get.mockResolvedValue(job("running"));
    const { api, wrapper } = harness(() => undefined);
    api.follow(job("running"));
    wrapper.unmount();
    await vi.advanceTimersByTimeAsync(POLL_MS * 3);
    expect(get).not.toHaveBeenCalled();
  });
});
