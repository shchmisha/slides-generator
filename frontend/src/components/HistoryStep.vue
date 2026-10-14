<script setup lang="ts">
import { computed } from "vue";

import type { Job } from "../api/types";
import { useWorkspace } from "../composables/workspace";
import { elapsed, seconds, when } from "../lib/format";

const ws = useWorkspace();
const rows = computed(() => ws.history.value);

const STATUS: Record<Job["status"], { text: string; badge: string }> = {
  queued: { text: "в очереди", badge: "badge" },
  running: { text: "идёт", badge: "badge badge-run" },
  succeeded: { text: "готово", badge: "badge badge-ok" },
  failed: { text: "ошибка", badge: "badge badge-bad" },
};

/** The brief asks for time split by stage: files, model, render. */
function stages(job: Job): string {
  const t = job.timings;
  const parts =
    job.type === "plan"
      ? [
          ["очередь", t.queue_ms],
          ["разбор файлов", t.parse_ms],
          ["данные", t.facts_ms],
          [`модель${t.attempts ? ` (${t.attempts} поп.)` : ""}`, t.llm_ms],
        ]
      : [
          ["подготовка", t.prepare_ms],
          ["вёрстка", t.layout_ms],
          ["PPTX", t.pptx_ms],
          ["сохранение", t.save_ms],
        ];
  return parts
    .filter(([, ms]) => typeof ms === "number")
    .map(([name, ms]) => `${name} ${seconds(ms as number)}`)
    .join(" · ");
}
</script>

<template>
  <section class="card">
    <h2>История запусков</h2>
    <p v-if="!rows.length" class="empty">Запусков пока не было</p>
    <div v-else class="table-wrap">
      <table class="data">
        <thead>
          <tr>
            <th>Когда</th>
            <th>Что</th>
            <th>Статус</th>
            <th class="num">Всего</th>
            <th>По этапам</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="job in rows" :key="job.id">
            <td>{{ when(job.created_at) }}</td>
            <td>
              {{ job.type === "plan" ? "Сценарий" : "Сборка" }}
              <span v-if="job.scenario_version" class="muted small"
                >· версия {{ job.scenario_version }}</span
              >
            </td>
            <td>
              <span :class="STATUS[job.status].badge">{{ STATUS[job.status].text }}</span>
              <p v-if="job.error" class="small alert-bad error">{{ job.error }}</p>
            </td>
            <td class="num">{{ seconds(elapsed(job.created_at, job.finished_at)) }}</td>
            <td class="small muted">{{ stages(job) || "—" }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
.error {
  margin: 4px 0 0;
  padding: 2px 6px;
  border-radius: 4px;
}
</style>
