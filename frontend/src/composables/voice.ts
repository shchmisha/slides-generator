/** Whether this user's videos get a voice: loaded on demand, forgotten on sign-out. */

import { ref, watch } from "vue";

import { voice } from "../api/endpoints";
import type { VoiceStatus } from "../api/types";
import { currentUser } from "./session";

export const voiceStatus = ref<VoiceStatus | null>(null);

watch(currentUser, (now, before) => {
  if (now?.id !== before?.id) voiceStatus.value = null;
});

export async function loadVoice(force = false): Promise<VoiceStatus> {
  if (!voiceStatus.value || force) voiceStatus.value = await voice.status();
  return voiceStatus.value;
}

export function setVoice(status: VoiceStatus): void {
  voiceStatus.value = status;
}
