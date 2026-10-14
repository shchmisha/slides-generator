<script setup lang="ts">
import { computed } from "vue";

import type { DeckContent, DeckReport, SpecSlide } from "../api/types";
import { useEditing } from "../composables/editing";
import { errorsByIndex, humanize, move } from "../lib/scenario";
import SlideCard from "./SlideCard.vue";
import SlidePreview from "./SlidePreview.vue";
import TextField from "./TextField.vue";
import ValuePicker from "./ValuePicker.vue";

const props = defineProps<{
  report: DeckReport;
  stale: boolean;
  errors: string[];
  fixedCount: boolean;
}>();
/** Slides moved or removed: the server's errors point at old positions. */
const emit = defineEmits<{ changed: [] }>();

const editing = useEditing();
const deck = computed(() => editing.draft.value as DeckContent);
const grouped = computed(() => errorsByIndex(props.errors, "slides"));

/**
 * For each resolved slide, the scenario slide it came from. Versions saved before the server
 * reported it: one to one when the counts agree, otherwise unknown (shown as after an edit).
 */
const origins = computed<(number | null)[] | null>(() => {
  if (props.report.preview_origins) return props.report.preview_origins;
  const slides = props.report.preview?.slides ?? [];
  return slides.length === deck.value.slides.length ? slides.map((_, i) => i) : null;
});
const unknown = computed(() => props.stale || origins.value === null);

/** The resolved slide built from scenario slide `index`. */
function previewFor(index: number): SpecSlide | null {
  const at = origins.value?.indexOf(index) ?? -1;
  return at >= 0 ? (props.report.preview?.slides[at] ?? null) : null;
}

const added = computed(() =>
  (origins.value ?? [])
    .map((origin, i) => (origin === null ? props.report.preview?.slides[i] : undefined))
    .filter((s): s is SpecSlide => s !== undefined),
);

function reorder(from: number, to: number): void {
  move(deck.value.slides, from, to);
  emit("changed");
}

function remove(index: number): void {
  if (!window.confirm(`Удалить слайд ${index + 1} из сценария?`)) return;
  deck.value.slides.splice(index, 1);
  emit("changed");
}
</script>

<template>
  <div class="editor">
    <div class="main">
      <section class="card">
        <TextField v-model="deck.title" label="Название презентации" :max="120" />
        <TextField v-model="deck.footer" label="Подпись внизу слайдов" :max="120" optional />
        <div v-if="grouped.general.length" class="alert alert-bad small general" role="alert">
          <ul>
            <li v-for="e in grouped.general" :key="e">{{ humanize(e) }}</li>
          </ul>
        </div>
      </section>
      <SlideCard
        v-for="(_, i) in deck.slides"
        :key="`${i}-${deck.slides.length}`"
        :index="i"
        :count="deck.slides.length"
        :preview="unknown ? null : previewFor(i)"
        :stale="unknown"
        :fixed-count="fixedCount"
        :errors="grouped.byIndex.get(i) ?? []"
        @move="(to) => reorder(i, to)"
        @remove="remove(i)"
      />
      <article v-for="(s, i) in unknown ? [] : added" :key="`auto${i}`" class="card">
        <h3>{{ deck.slides.length + i + 1 }}. {{ s.title }}</h3>
        <p class="muted small">Добавлен автоматически: в данных есть что показать</p>
        <SlidePreview :slide="s" />
      </article>
    </div>
    <ValuePicker />
  </div>
</template>

<style scoped>
.editor {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 300px;
  gap: 16px;
  align-items: start;
}
@media (max-width: 1100px) {
  .editor {
    grid-template-columns: minmax(0, 1fr);
  }
}
.main > * + * {
  margin-top: 16px;
}
.general {
  margin-top: 10px;
}
</style>
