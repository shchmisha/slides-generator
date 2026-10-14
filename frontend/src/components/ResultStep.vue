<script setup lang="ts">
import { computed, ref } from "vue";

import { errorText } from "../api/client";

import { isActive, type Artifact, type Job } from "../api/types";
import { useWorkspace } from "../composables/workspace";
import { seconds, size, when } from "../lib/format";
import JobProgress from "./JobProgress.vue";

const ws = useWorkspace();
const project = computed(() => ws.project.value!);

interface Render {
  job: Job;
  file: Artifact | null;
  previews: Artifact[];
}

/** Finished renders, newest first, with their files (old renders may have none left). */
const renders = computed<Render[]>(() =>
  ws.history.value
    .filter((j) => j.type === "render" && j.status === "succeeded")
    .map((job) => {
      const own = ws.outputs.value.filter((a) => a.job_id === job.id);
      return {
        job,
        file: own.find((a) => a.kind === "pptx" || a.kind === "mp4") ?? null,
        previews: own.filter((a) => a.kind === "preview").sort((a, b) => a.position - b.position),
      };
    }),
);
const latest = computed(() => renders.value[0] ?? null);
const older = computed(() => renders.value.slice(1));
const rendering = computed(() => isActive(ws.renderJob.value));
const failed = computed(() =>
  ws.renderJob.value?.status === "failed" ? ws.renderJob.value : null,
);

const retrying = ref(false);
const retryError = ref<string | null>(null);

/** Build the same scenario version again (e.g. after a timeout). */
async function retry(): Promise<void> {
  retrying.value = true;
  retryError.value = null;
  try {
    await ws.startRender(failed.value?.scenario_version ?? undefined);
  } catch (err) {
    retryError.value = errorText(err);
  } finally {
    retrying.value = false;
  }
}

const ISSUE_TEXT: Record<string, string> = {
  shrunk: "текст уменьшен, чтобы поместиться",
  overflow: "текст не помещается — сократите его",
  outside: "элемент выходит за край слайда",
};
</script>

<template>
  <section v-if="project.kind === 'video'" class="card empty">
    <p>
      Сборка ролика появится в следующей версии сервиса. Сценарий уже можно составить и проверить.
    </p>
  </section>

  <template v-else>
    <section v-if="rendering && ws.renderJob.value" class="card">
      <JobProgress :job="ws.renderJob.value" title="Собираем презентацию" />
    </section>
    <div v-else-if="failed" class="alert alert-bad spread" role="alert">
      <span>Сборка не удалась: {{ failed.error }}</span>
      <button type="button" class="secondary" :disabled="retrying" @click="retry">
        Собрать ещё раз
      </button>
    </div>
    <p v-if="retryError" class="alert alert-bad">{{ retryError }}</p>

    <section v-if="latest" class="card stack">
      <div class="spread">
        <div>
          <h2>{{ rendering || failed ? "Предыдущая сборка" : "Презентация готова" }}</h2>
          <p class="muted small">
            Сценарий версии {{ latest.job.scenario_version }} · собрана
            {{ when(latest.job.finished_at) }} · вёрстка и сборка
            {{ seconds(latest.job.timings.render_ms) }}
          </p>
        </div>
        <a v-if="latest.file" :href="latest.file.url" class="button" download>
          Скачать PPTX ({{ size(latest.file.size) }})
        </a>
      </div>
      <div v-if="latest.job.report.warnings?.length" class="alert alert-warn small">
        <ul>
          <li v-for="w in latest.job.report.warnings" :key="w">{{ w }}</li>
        </ul>
      </div>
      <div v-if="latest.job.report.issues?.length" class="alert alert-info small">
        Вёрстка:
        <ul>
          <li v-for="(issue, i) in latest.job.report.issues" :key="i">
            слайд {{ issue.slide + 1 }}: {{ ISSUE_TEXT[issue.kind] ?? issue.kind }}
          </li>
        </ul>
      </div>
      <div class="previews">
        <a v-for="p in latest.previews" :key="p.id" :href="p.url" target="_blank" rel="noopener">
          <img :src="p.url" :alt="`Слайд ${p.position}`" loading="lazy" />
        </a>
      </div>
    </section>
    <section v-else-if="!rendering" class="card empty">
      <p>Презентация ещё не собрана.</p>
      <RouterLink
        :to="{ name: 'project', params: { id: project.id, step: 'scenario' } }"
        class="button"
      >
        Проверить сценарий и собрать
      </RouterLink>
    </section>

    <section v-if="older.length" class="card">
      <h2>Предыдущие сборки</h2>
      <div class="table-wrap">
        <table class="data">
          <tbody>
            <tr v-for="r in older" :key="r.job.id">
              <td>{{ when(r.job.finished_at) }}</td>
              <td>версия сценария {{ r.job.scenario_version }}</td>
              <td class="num">
                <a v-if="r.file" :href="r.file.url" download>Скачать</a>
                <span v-else class="muted small">файлы удалены (хранятся последние 5 сборок)</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </template>
</template>

<style scoped>
.previews {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 12px;
}
.previews img {
  width: 100%;
  aspect-ratio: 16 / 9;
  object-fit: cover;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #fff;
}
section + section {
  margin-top: 16px;
}
</style>
