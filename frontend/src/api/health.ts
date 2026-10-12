import { ApiError, apiGet } from "./client";

export type CheckStatus = "ok" | "fail";

export interface Health {
  status: CheckStatus;
  database: CheckStatus;
  redis: CheckStatus;
}

export interface Workers {
  status: CheckStatus;
  queues: Record<string, number>;
}

/** Health endpoints answer 503 with a body; surface that body instead of throwing. */
async function getAllowingUnavailable<T>(path: string): Promise<T | null> {
  try {
    return await apiGet<T>(path);
  } catch (err) {
    if (err instanceof ApiError && err.status === 503) return null;
    throw err;
  }
}

export const fetchHealth = () => getAllowingUnavailable<Health>("/health");
export const fetchWorkers = () => getAllowingUnavailable<Workers>("/health/workers");
