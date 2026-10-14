<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from "vue";

import type { Json, ProjectKind } from "../api/types";
import { useEditing } from "../composables/editing";
import { humanize, same } from "../lib/scenario";

/**
 * The scenario as JSON, for what the form does not cover (figures, charts, new slides).
 * Valid JSON is applied as you type; text that does not parse or has the wrong shape keeps
 * the scenario marked unsaved and blocks saving, so nothing typed here is dropped silently.
 */
const props = defineProps<{ kind: ProjectKind; errors: string[] }>();
const editing = useEditing();
const text = ref("");
const error = ref<string | null>(null);
let timer: ReturnType<typeof setTimeout> | null = null;

function matchesDraft(): boolean {
  try {
    return same(JSON.parse(text.value), editing.draft.value);
  } catch {
    return false;
  }
}

watch(
  editing.draft,
  (draft) => {
    if (!matchesDraft()) text.value = JSON.stringify(draft, null, 2);
  },
  { immediate: true, deep: true },
);

function shapeError(value: unknown): string | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return "Нужен объект JSON";
  const scenario = value as Json;
  if (scenario.kind !== props.kind) return `Поле kind должно быть "${props.kind}"`;
  const list = props.kind === "deck" ? "slides" : "scenes";
  const items = scenario[list];
  if (!Array.isArray(items) || !items.length || !items.every((i) => i && typeof i === "object")) {
    return `Поле ${list} должно быть непустым списком объектов`;
  }
  return null;
}

function apply(): void {
  if (timer !== null) clearTimeout(timer);
  timer = null;
  try {
    const parsed: unknown = JSON.parse(text.value);
    error.value = shapeError(parsed);
    if (error.value === null) editing.draft.value = parsed as Json;
  } catch (err) {
    error.value = `Ошибка в JSON: ${err instanceof Error ? err.message : "не читается"}`;
  }
  editing.jsonInvalid.value = error.value !== null;
}

function onInput(): void {
  editing.jsonInvalid.value = true; // until applied
  if (timer !== null) clearTimeout(timer);
  timer = setTimeout(apply, 300);
}

onMounted(() => {
  editing.flush.value = () => {
    if (timer !== null) apply();
  };
});
onBeforeUnmount(() => {
  if (timer !== null) apply();
  editing.flush.value = null;
});
</script>

<template>
  <section class="card stack">
    <p class="muted small">
      Сценарий целиком: имена значений (<code>values</code>), расчёты (<code>derived</code>), слайды
      и утверждения из документов (<code>claims</code>). Цифры пишутся только через
      <code>{имя}</code>; при сохранении сервис проверит всё так же, как ответ модели.
    </p>
    <textarea
      v-model="text"
      class="json"
      spellcheck="false"
      aria-label="Сценарий в JSON"
      :class="{ 'field-error': error }"
      @input="onInput"
    />
    <p v-if="error" class="alert alert-bad small" role="alert">{{ error }}</p>
    <div v-if="errors.length" class="alert alert-bad small" role="alert">
      Сервер не принял сценарий:
      <ul>
        <li v-for="e in errors" :key="e">{{ humanize(e) }}</li>
      </ul>
    </div>
  </section>
</template>

<style scoped>
.json {
  min-height: 480px;
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  font-size: 13px;
}
</style>
