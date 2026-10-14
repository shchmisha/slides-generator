/** Who is signed in. Loaded once from /auth/me; cleared on sign-out or any 401. */

import { ref } from "vue";

import { ApiError } from "../api/client";
import { auth } from "../api/endpoints";
import type { User } from "../api/types";

export const currentUser = ref<User | null>(null);
let loaded = false;

export async function loadUser(): Promise<User | null> {
  if (loaded) return currentUser.value;
  try {
    currentUser.value = await auth.me();
  } catch (err) {
    if (!(err instanceof ApiError && err.status === 401)) throw err;
    currentUser.value = null;
  }
  loaded = true;
  return currentUser.value;
}

export function setUser(user: User | null): void {
  currentUser.value = user;
  loaded = true;
}
