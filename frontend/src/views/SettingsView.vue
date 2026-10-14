<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import { errorText } from "../api/client";
import { voice } from "../api/endpoints";
import type { VoiceInfo } from "../api/types";
import { loadVoice, setVoice, voiceStatus } from "../composables/voice";
import { when } from "../lib/format";

const loading = ref(true);
const loadError = ref<string | null>(null);
const keyInput = ref("");
const busy = ref(false);
const error = ref<string | null>(null);
const notice = ref<string | null>(null);
const voices = ref<VoiceInfo[]>([]);
const voicesError = ref<string | null>(null);

const enabled = computed(() => voiceStatus.value?.enabled ?? false);

async function loadVoices(): Promise<void> {
  voicesError.value = null;
  try {
    voices.value = await voice.voices();
  } catch (err) {
    voicesError.value = errorText(err);
  }
}

onMounted(async () => {
  try {
    if ((await loadVoice(true)).enabled) await loadVoices();
  } catch (err) {
    loadError.value = errorText(err);
  } finally {
    loading.value = false;
  }
});

async function saveKey(): Promise<void> {
  busy.value = true;
  error.value = notice.value = null;
  try {
    setVoice(await voice.setKey(keyInput.value.trim()));
    notice.value = "Ключ проверен и сохранён. Новые ролики будут с озвучкой";
    await loadVoices();
  } catch (err) {
    error.value = errorText(err);
  } finally {
    keyInput.value = ""; // never keep the key around in the page
    busy.value = false;
  }
}

async function chooseVoice(event: Event): Promise<void> {
  const id = (event.target as HTMLSelectElement).value;
  busy.value = true;
  error.value = notice.value = null;
  try {
    setVoice(await voice.choose(id));
    notice.value = "Голос выбран";
  } catch (err) {
    error.value = errorText(err);
  } finally {
    busy.value = false;
  }
}

async function removeKey(): Promise<void> {
  if (!window.confirm("Удалить ключ ElevenLabs? Новые ролики будут без звука")) return;
  busy.value = true;
  error.value = notice.value = null;
  try {
    await voice.remove();
    await loadVoice(true);
    voices.value = [];
    notice.value = "Ключ удалён";
  } catch (err) {
    error.value = errorText(err);
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <h1>Настройки</h1>
  <p v-if="loadError" class="alert alert-bad" role="alert">{{ loadError }}</p>
  <section v-else-if="!loading && voiceStatus" class="card stack voice">
    <div class="spread">
      <h2>Озвучка роликов</h2>
      <span class="badge" :class="enabled ? 'badge-ok' : ''">
        {{ enabled ? "включена" : "выключена" }}
      </span>
    </div>
    <p class="muted small">
      Голос диктора и звуки в ролике появляются, только если добавлен ваш ключ
      <a href="https://elevenlabs.io/app/developers/api-keys" target="_blank" rel="noopener"
        >ElevenLabs</a
      >. Без ключа ролики собираются без звука, с субтитрами. Ключ хранится зашифрованным и больше
      нигде не показывается; озвучка расходует кредиты вашего аккаунта ElevenLabs.
    </p>

    <p v-if="enabled" class="small">
      Голос <strong>{{ voiceStatus.voice_name }}</strong> · модель {{ voiceStatus.model }} · ключ
      добавлен {{ when(voiceStatus.updated_at) }}
    </p>

    <div v-if="enabled">
      <label for="voice-select">Голос диктора</label>
      <select
        id="voice-select"
        :value="voiceStatus.voice_id ?? ''"
        :disabled="busy || !voices.length"
        @change="chooseVoice"
      >
        <option v-if="!voices.length" :value="voiceStatus.voice_id ?? ''">
          {{ voiceStatus.voice_name }}
        </option>
        <option v-for="v in voices" :key="v.id" :value="v.id">
          {{ v.name }}{{ v.description ? ` — ${v.description}` : "" }}
        </option>
      </select>
      <p v-if="voicesError" class="small alert-bad">
        Список голосов не загрузился: {{ voicesError }}
      </p>
    </div>

    <form class="stack" @submit.prevent="saveKey">
      <div>
        <label for="voice-key">{{ enabled ? "Заменить ключ" : "Ключ API ElevenLabs" }}</label>
        <input
          id="voice-key"
          v-model="keyInput"
          type="password"
          autocomplete="off"
          spellcheck="false"
          placeholder="sk_…"
          required
        />
        <p class="muted small">
          Ключ создаётся в ElevenLabs: Developers → API Keys. Нужны разрешения «Text to Speech»,
          «Sound Effects» и «Voices: read».
        </p>
      </div>
      <div class="row">
        <button type="submit" :disabled="busy || keyInput.trim().length < 16">
          {{ busy ? "Проверяем…" : "Проверить и сохранить" }}
        </button>
        <button v-if="enabled" type="button" class="secondary" :disabled="busy" @click="removeKey">
          Удалить ключ
        </button>
      </div>
    </form>
    <p v-if="error" class="alert alert-bad" role="alert">{{ error }}</p>
    <p v-if="notice" class="alert alert-info" role="status">{{ notice }}</p>
  </section>
</template>

<style scoped>
.voice {
  max-width: 720px;
}
</style>
