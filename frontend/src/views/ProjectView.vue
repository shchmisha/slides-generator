<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted } from "vue";
import { onBeforeRouteLeave, onBeforeRouteUpdate } from "vue-router";

import MaterialsStep from "../components/MaterialsStep.vue";
import ResultStep from "../components/ResultStep.vue";
import ScenarioStep from "../components/ScenarioStep.vue";
import HistoryStep from "../components/HistoryStep.vue";
import { provideWorkspace } from "../composables/workspace";
import type { Step } from "../router";

const props = defineProps<{ id: string; step?: Step }>();

const ws = provideWorkspace(props.id);
const step = computed<Step>(() => props.step ?? (ws.scenario.value ? "scenario" : "materials"));

const TABS: { step: Step; title: string }[] = [
  { step: "materials", title: "1. Материалы и оформление" },
  { step: "scenario", title: "2. Сценарий" },
  { step: "result", title: "3. Результат" },
  { step: "history", title: "История запусков" },
];

onMounted(() => void ws.load());

/* Unsaved scenario edits: ask before they are lost (other project, sign-out, closing the tab). */
const LEAVE = "В сценарии есть несохранённые правки. Уйти без сохранения?";
onBeforeRouteLeave(() => !ws.dirty.value || window.confirm(LEAVE));
onBeforeRouteUpdate((to) => to.params.id === props.id || !ws.dirty.value || window.confirm(LEAVE));
function beforeUnload(event: BeforeUnloadEvent): void {
  if (ws.dirty.value) event.preventDefault();
}
window.addEventListener("beforeunload", beforeUnload);
onBeforeUnmount(() => window.removeEventListener("beforeunload", beforeUnload));
</script>

<template>
  <p v-if="ws.error.value" class="alert alert-bad" role="alert">{{ ws.error.value }}</p>
  <template v-if="ws.project.value">
    <div class="spread heading">
      <div>
        <RouterLink to="/projects" class="small">← Все проекты</RouterLink>
        <h1>{{ ws.project.value.title }}</h1>
      </div>
      <span class="badge">{{ ws.project.value.kind === "deck" ? "Презентация" : "Ролик" }}</span>
    </div>
    <nav class="tabs" aria-label="Шаги">
      <RouterLink
        v-for="tab in TABS"
        :key="tab.step"
        :to="{ name: 'project', params: { id, step: tab.step } }"
        :aria-current="step === tab.step ? 'page' : undefined"
        >{{ tab.title }}</RouterLink
      >
    </nav>
    <MaterialsStep v-if="step === 'materials'" />
    <ScenarioStep v-else-if="step === 'scenario'" />
    <ResultStep v-else-if="step === 'result'" />
    <HistoryStep v-else />
  </template>
  <p v-else-if="!ws.error.value" class="muted">Загружаем проект…</p>
</template>

<style scoped>
.heading {
  margin-bottom: 8px;
}
.heading h1 {
  margin: 6px 0 0;
}
</style>
