<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";

import { ApiError, errorText } from "../api/client";
import { scenarios } from "../api/endpoints";
import {
  isActive,
  type DeckReport,
  type DeckSettings,
  type VideoReport,
  type ScenarioSummary,
  type ValueInfo,
  type VideoSettings,
} from "../api/types";
import { provideEditing } from "../composables/editing";
import { loadVoice, voiceStatus } from "../composables/voice";
import { useWorkspace } from "../composables/workspace";
import { when } from "../lib/format";
import { clone, humanize } from "../lib/scenario";
import DeckEditor from "./DeckEditor.vue";
import JobProgress from "./JobProgress.vue";
import JsonPanel from "./JsonPanel.vue";
import ReviewNotes from "./ReviewNotes.vue";
import VideoEditor from "./VideoEditor.vue";

const ws = useWorkspace();
const router = useRouter();

const draft = ws.draft;
const values = computed<ValueInfo[]>(
  () => (ws.scenario.value?.report as DeckReport | undefined)?.values ?? [],
);
const editing = provideEditing(draft, values, ws.jsonInvalid);

const mode = ref<"form" | "json">("form");
const versions = ref<ScenarioSummary[]>([]);
const errors = ref<string[]>([]);
const message = ref<{ kind: "ok" | "bad"; text: string } | null>(null);
const busy = ref(false);

const scenario = computed(() => ws.scenario.value);
const project = computed(() => ws.project.value!);
const dirty = ws.dirty;
const latest = computed(() => versions.value[0]?.version ?? scenario.value?.version ?? 0);
const planning = computed(() => isActive(ws.planJob.value));
const planFailed = computed(() => {
  const job = ws.planJob.value;
  if (job?.status !== "failed") return false;
  const shown = scenario.value?.created_at;
  return !shown || new Date(job.created_at) > new Date(shown);
});

watch(scenario, () => {
  errors.value = [];
  void loadVersions();
});

async function loadVersions(): Promise<void> {
  try {
    versions.value = await scenarios.versions(project.value.id);
  } catch {
    versions.value = [];
  }
}

async function openVersion(event: Event): Promise<void> {
  const version = Number((event.target as HTMLSelectElement).value);
  if (dirty.value && !window.confirm("Несохранённые правки пропадут. Открыть другую версию?")) {
    (event.target as HTMLSelectElement).value = String(scenario.value?.version);
    return;
  }
  message.value = null;
  await ws
    .reloadScenario(version)
    .catch((err) => (message.value = { kind: "bad", text: errorText(err) }));
}

/** Save as a new version on top of the latest one. Returns false if the server refused it. */
function setMode(next: "form" | "json"): void {
  editing.flush.value?.();
  if (next === "form" && ws.jsonInvalid.value) {
    if (!window.confirm("JSON с ошибкой не применён. Вернуться к форме и отменить эти правки?")) {
      return;
    }
    ws.jsonInvalid.value = false;
  }
  mode.value = next;
}

async function save(): Promise<boolean> {
  editing.flush.value?.();
  if (!draft.value) return false;
  if (ws.jsonInvalid.value) {
    message.value = { kind: "bad", text: "В расширенном режиме JSON с ошибкой — исправьте его" };
    mode.value = "json";
    return false;
  }
  busy.value = true;
  message.value = null;
  errors.value = [];
  try {
    ws.scenario.value = await scenarios.save(project.value.id, latest.value, draft.value);
    message.value = { kind: "ok", text: `Сохранено: версия ${ws.scenario.value.version}` };
    return true;
  } catch (err) {
    if (err instanceof ApiError && err.errors.length) {
      errors.value = err.errors;
      message.value = { kind: "bad", text: "Сценарий не прошёл проверку — исправьте отмеченное" };
    } else {
      message.value = { kind: "bad", text: errorText(err) };
    }
    return false;
  } finally {
    busy.value = false;
  }
}

function discard(): void {
  if (scenario.value) draft.value = clone(scenario.value.content);
  ws.jsonInvalid.value = false;
  errors.value = [];
  message.value = null;
}

async function build(): Promise<void> {
  if (dirty.value && !(await save())) return;
  busy.value = true;
  try {
    await ws.startRender(scenario.value?.version);
    await router.push({ name: "project", params: { id: project.value.id, step: "result" } });
  } catch (err) {
    message.value = { kind: "bad", text: errorText(err) };
  } finally {
    busy.value = false;
  }
}

async function replan(): Promise<void> {
  try {
    await ws.startPlan();
  } catch (err) {
    message.value = { kind: "bad", text: errorText(err) };
  }
}

onMounted(() => {
  void loadVersions();
  // The build button says whether the video will have a voice; not knowing is fine.
  if (project.value.kind === "video") void loadVoice().catch(() => undefined);
});
</script>

<template>
  <section v-if="planning && ws.planJob.value" class="card">
    <JobProgress :job="ws.planJob.value" title="Составляем сценарий" />
    <p class="muted small">
      Обычно это 20–60 секунд: модель пишет сценарий, сервис проверяет каждую цифру и при
      необходимости просит модель исправиться.
    </p>
  </section>

  <section v-else-if="planFailed && !scenario" class="card stack">
    <p class="alert alert-bad">{{ ws.planJob.value?.error }}</p>
    <details v-if="ws.planJob.value?.report.errors?.length">
      <summary>Что не прошло проверку</summary>
      <ul class="small">
        <li v-for="e in ws.planJob.value.report.errors" :key="e">{{ humanize(e) }}</li>
      </ul>
    </details>
    <div class="row">
      <button type="button" @click="replan">Попробовать ещё раз</button>
      <RouterLink
        :to="{ name: 'project', params: { id: project.id, step: 'materials' } }"
        class="button secondary"
      >
        К материалам
      </RouterLink>
    </div>
  </section>

  <section v-else-if="!scenario" class="card empty">
    <p>Сценария пока нет.</p>
    <RouterLink
      :to="{ name: 'project', params: { id: project.id, step: 'materials' } }"
      class="button"
    >
      Добавить материалы и составить сценарий
    </RouterLink>
  </section>

  <template v-else-if="draft">
    <section class="card toolbar">
      <div class="spread">
        <div class="row">
          <label for="version" class="inline">Версия</label>
          <select id="version" :value="scenario.version" class="version" @change="openVersion">
            <option v-for="v in versions" :key="v.id" :value="v.version">
              {{ v.version }} · {{ v.source === "model" ? "модель" : "правка" }} ·
              {{ when(v.created_at) }}
            </option>
          </select>
          <span v-if="dirty" class="badge badge-run">есть несохранённые правки</span>
          <span v-else-if="scenario.version < latest" class="badge"
            >старая версия: сохранение станет новой</span
          >
        </div>
        <div class="row">
          <div class="tabs mode" role="tablist">
            <a
              href="#"
              role="tab"
              :aria-current="mode === 'form' ? 'page' : undefined"
              :aria-selected="mode === 'form'"
              @click.prevent="setMode('form')"
              >Форма</a
            >
            <a
              href="#"
              role="tab"
              :aria-current="mode === 'json' ? 'page' : undefined"
              :aria-selected="mode === 'json'"
              @click.prevent="setMode('json')"
              >Расширенный режим (JSON)</a
            >
          </div>
        </div>
      </div>
      <div class="spread actions">
        <p
          v-if="message"
          class="alert"
          :class="message.kind === 'ok' ? 'alert-ok' : 'alert-bad'"
          role="status"
        >
          {{ message.text }}
        </p>
        <span v-else class="muted small"
          >Правки проверяются при сохранении так же, как ответ модели</span
        >
        <div class="row">
          <button type="button" class="icon" :disabled="!dirty || busy" @click="discard">
            Отменить правки
          </button>
          <button type="button" class="secondary" :disabled="!dirty || busy" @click="save">
            Сохранить
          </button>
          <button type="button" :disabled="busy || isActive(ws.renderJob.value)" @click="build">
            {{ dirty ? "Сохранить и собрать" : "Собрать" }}
            {{ project.kind === "deck" ? "PPTX" : "MP4" }}
          </button>
        </div>
      </div>
      <p v-if="project.kind === 'video' && voiceStatus" class="muted small voice-line">
        <template v-if="voiceStatus.enabled">
          Озвучка: голос {{ voiceStatus.voice_name }} (ElevenLabs){{
            voiceStatus.sounds ? ", фоновая музыка и звуки переходов" : ""
          }}.
        </template>
        <template v-else>Ролик будет без звука, с субтитрами.</template>
        <RouterLink to="/settings">{{
          voiceStatus.enabled ? "Изменить" : "Добавить ключ ElevenLabs"
        }}</RouterLink>
      </p>
      <p v-if="planFailed" class="alert alert-warn small">
        Последняя попытка составить сценарий не удалась: {{ ws.planJob.value?.error }}. Показана
        предыдущая версия.
      </p>
    </section>

    <ReviewNotes :report="scenario.report" />

    <JsonPanel v-if="mode === 'json'" :kind="project.kind" :errors="errors" />
    <DeckEditor
      v-else-if="project.kind === 'deck'"
      :report="scenario.report as DeckReport"
      :stale="dirty"
      :errors="errors"
      :fixed-count="(project.settings as DeckSettings).slides != null"
      @changed="errors = []"
    />
    <VideoEditor
      v-else
      :report="scenario.report as VideoReport"
      :settings="project.settings as VideoSettings"
      :errors="errors"
      @changed="errors = []"
    />
  </template>
</template>

<style scoped>
.toolbar {
  position: sticky;
  top: 0;
  z-index: 2;
}
@media (max-width: 700px) {
  .toolbar {
    position: static; /* on a phone it would cover half the screen */
  }
}
.inline {
  margin: 0;
}
.version {
  width: auto;
  min-width: 260px;
}
.mode {
  margin: 0;
  border: none;
}
.actions {
  margin-top: 10px;
}
.actions .alert {
  margin: 0;
}
section + section,
section + .editor,
section + article {
  margin-top: 16px;
}
summary {
  cursor: pointer;
  font-weight: 600;
}
</style>
