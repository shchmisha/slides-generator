/**
 * Follow a background job: poll its status every second until it finishes. Network errors
 * slow the polling down instead of stopping it, so a short outage does not lose the job.
 */

import { onBeforeUnmount, ref, type Ref } from "vue";

import { errorText } from "../api/client";
import { jobs } from "../api/endpoints";
import { isActive, type Job } from "../api/types";

export const POLL_MS = 1000;
const MAX_BACKOFF_MS = 8000;

export function useJob(onFinished?: (job: Job) => void): {
  job: Ref<Job | null>;
  error: Ref<string | null>;
  follow: (job: Job) => void;
  stop: () => void;
} {
  const job = ref<Job | null>(null);
  const error = ref<string | null>(null);
  let timer: ReturnType<typeof setTimeout> | null = null;
  let delay = POLL_MS;
  let current = 0; // ignores answers for a job no longer followed

  function stop(): void {
    if (timer !== null) clearTimeout(timer);
    timer = null;
    current += 1;
  }

  function schedule(id: string, token: number): void {
    timer = setTimeout(() => void poll(id, token), delay);
  }

  async function poll(id: string, token: number): Promise<void> {
    try {
      const fresh = await jobs.get(id);
      if (token !== current) return;
      job.value = fresh;
      error.value = null;
      delay = POLL_MS;
      if (isActive(fresh)) schedule(id, token);
      else onFinished?.(fresh);
    } catch (err) {
      if (token !== current) return;
      error.value = errorText(err);
      delay = Math.min(delay * 2, MAX_BACKOFF_MS);
      schedule(id, token);
    }
  }

  function follow(started: Job): void {
    stop();
    job.value = started;
    error.value = null;
    delay = POLL_MS;
    if (isActive(started)) schedule(started.id, current);
    else onFinished?.(started);
  }

  onBeforeUnmount(stop);
  return { job, error, follow, stop };
}
