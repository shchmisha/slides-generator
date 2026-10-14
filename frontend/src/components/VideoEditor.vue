<script setup lang="ts">
import { computed } from "vue";

import type { VideoContent, VideoReport, VideoScene, VideoSettings } from "../api/types";
import { useEditing } from "../composables/editing";
import { errorsByIndex, humanize, move, shortPath, VISUAL_TYPES } from "../lib/scenario";
import TextField from "./TextField.vue";

const props = defineProps<{ report: VideoReport; settings: VideoSettings; errors: string[] }>();
const emit = defineEmits<{ changed: [] }>();

// The range the server checks against: the one the scenario was planned with.
const range = computed(() => ({
  min: props.report.min_seconds ?? props.settings.min_seconds,
  max: props.report.max_seconds ?? props.settings.max_seconds,
}));

const editing = useEditing();
const video = computed(() => editing.draft.value as VideoContent);
const grouped = computed(() => errorsByIndex(props.errors, "scenes"));
// Rounded to what is shown: durations scaled by the server can sum to 75.00000001.
const total = computed(
  () =>
    Math.round(video.value.scenes.reduce((sum, s) => sum + (Number(s.duration_s) || 0), 0) * 10) /
    10,
);
const inRange = computed(() => total.value >= range.value.min && total.value <= range.value.max);
const WITH_ITEMS = ["bullets", "steps", "comparison"];

function addScene(after: number): void {
  const scene: VideoScene = {
    duration_s: 8,
    narration: "Новая сцена.",
    visual: { type: "statement", heading: "Новая сцена" },
  };
  video.value.scenes.splice(after + 1, 0, scene);
  emit("changed");
}

function reorder(from: number, to: number): void {
  move(video.value.scenes, from, to);
  emit("changed");
}

function remove(index: number): void {
  if (!window.confirm(`Удалить сцену ${index + 1}?`)) return;
  video.value.scenes.splice(index, 1);
  emit("changed");
}
</script>

<template>
  <section class="card">
    <TextField v-model="video.title" label="Название ролика" :max="90" />
    <p class="small total" :class="inRange ? 'muted' : 'alert alert-warn'">
      Длительность: {{ total.toFixed(1).replace(".", ",") }} с (нужно {{ settings.min_seconds }}–{{
        settings.max_seconds
      }}
      с){{ inRange ? "" : " — при сохранении сервис подгонит длительности сцен" }}
    </p>
    <div v-if="grouped.general.length" class="alert alert-bad small" role="alert">
      <ul>
        <li v-for="e in grouped.general" :key="e">{{ humanize(e) }}</li>
      </ul>
    </div>
  </section>

  <article v-for="(scene, i) in video.scenes" :key="`${i}-${video.scenes.length}`" class="card">
    <header class="spread">
      <h3>Сцена {{ i + 1 }}</h3>
      <div class="row">
        <button
          type="button"
          class="icon"
          :disabled="i === 0"
          aria-label="Выше"
          @click="reorder(i, i - 1)"
        >
          ↑
        </button>
        <button
          type="button"
          class="icon"
          :disabled="i === video.scenes.length - 1"
          aria-label="Ниже"
          @click="reorder(i, i + 1)"
        >
          ↓
        </button>
        <button
          type="button"
          class="icon"
          :disabled="video.scenes.length >= 14"
          @click="addScene(i)"
        >
          + После
        </button>
        <button type="button" class="icon" :disabled="video.scenes.length <= 4" @click="remove(i)">
          Удалить
        </button>
      </div>
    </header>
    <div v-if="grouped.byIndex.get(i)?.length" class="alert alert-bad small" role="alert">
      <ul>
        <li v-for="e in grouped.byIndex.get(i)" :key="e">{{ shortPath(e) }}</li>
      </ul>
    </div>
    <div class="scene">
      <div>
        <TextField v-model="scene.narration" label="Текст диктора" :max="420" :rows="4" />
        <div class="row duration">
          <label :for="`d${i}`">Длительность, с</label>
          <input
            :id="`d${i}`"
            v-model.number="scene.duration_s"
            type="number"
            min="3"
            max="15"
            step="0.5"
          />
        </div>
      </div>
      <div>
        <label :for="`v${i}`">Что на экране</label>
        <select :id="`v${i}`" v-model="scene.visual.type">
          <option v-for="(title, type) in VISUAL_TYPES" :key="type" :value="type">
            {{ title }}
          </option>
        </select>
        <TextField v-model="scene.visual.heading" label="Заголовок" :max="90" optional />
        <TextField
          v-if="scene.visual.type === 'big_number'"
          v-model="scene.visual.number"
          label="Цифра (как в тексте)"
          :max="90"
        />
        <template v-if="WITH_ITEMS.includes(scene.visual.type)">
          <TextField
            v-for="(_, k) in scene.visual.items ?? []"
            :key="k"
            v-model="scene.visual.items![k]"
            :label="`Пункт ${k + 1}`"
            :max="90"
          />
          <button
            type="button"
            class="secondary small-button"
            :disabled="(scene.visual.items?.length ?? 0) >= 5"
            @click="(scene.visual.items ??= []).push('Новый пункт')"
          >
            + Пункт
          </button>
        </template>
        <TextField v-model="scene.visual.caption" label="Подпись" :max="90" optional />
      </div>
    </div>
  </article>
</template>

<style scoped>
.scene {
  display: grid;
  grid-template-columns: minmax(0, 3fr) minmax(0, 2fr);
  gap: 16px;
  margin-top: 10px;
}
@media (max-width: 900px) {
  .scene {
    grid-template-columns: minmax(0, 1fr);
  }
}
.duration {
  margin-top: 8px;
}
.duration input {
  width: 90px;
}
.duration label {
  margin: 0;
}
.total {
  margin-top: 8px;
}
.small-button {
  padding: 4px 10px;
  font-size: 13px;
  margin-top: 6px;
}
article {
  margin-top: 16px;
}
h3 {
  margin: 0;
}
</style>
