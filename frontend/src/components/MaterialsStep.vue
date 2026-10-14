<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import { useRouter } from "vue-router";

import { errorText } from "../api/client";
import { projects } from "../api/endpoints";
import { isActive, type DeckSettings, type FileOut, type VideoSettings } from "../api/types";
import { useWorkspace } from "../composables/workspace";
import { size } from "../lib/format";
import BrandPanel from "./BrandPanel.vue";
import JobProgress from "./JobProgress.vue";

const ws = useWorkspace();
const router = useRouter();
const project = computed(() => ws.project.value!);

const ACCEPT = ".docx,.xlsx,.pptx,.txt,.md";
const MAX_FILES = 10;
const MAX_BYTES = 20 * 1024 * 1024;

/* ------------------------------------------------------------------ files */

interface Upload {
  name: string;
  state: "waiting" | "uploading" | "failed";
  message?: string;
}
const uploads = ref<Upload[]>([]);
const dragging = ref(false);
const fileError = ref<string | null>(null);

const uploading = computed(() => uploads.value.some((u) => u.state !== "failed"));

async function addFiles(files: FileList | File[]): Promise<void> {
  fileError.value = null;
  const list = Array.from(files);
  const tooBig = list.filter((f) => f.size > MAX_BYTES);
  const fitting = list.filter((f) => f.size <= MAX_BYTES);
  // Files already stored and files still in the queue both count towards the limit.
  const queued = uploads.value.filter((u) => u.state !== "failed").length;
  const room = Math.max(MAX_FILES - project.value.files.length - queued, 0);
  const problems = tooBig.map((f) => `«${f.name}» больше 20 МБ`);
  if (fitting.length > room) problems.push(`Можно добавить ещё ${room} из ${MAX_FILES} файлов`);
  fileError.value = problems.join(". ") || null;
  const queue = fitting.slice(0, room).map((file) => {
    const entry = reactive<Upload>({ name: file.name, state: "waiting" });
    uploads.value.push(entry);
    return { file, entry };
  });
  for (const { file, entry } of queue) {
    entry.state = "uploading";
    try {
      const stored = await projects.uploadFile(project.value.id, file);
      project.value.files.push(stored);
      uploads.value = uploads.value.filter((u) => u !== entry);
    } catch (err) {
      entry.state = "failed";
      entry.message = errorText(err);
    }
  }
}

function onPick(event: Event): void {
  const input = event.target as HTMLInputElement;
  if (input.files) void addFiles(input.files);
  input.value = "";
}

function onDrop(event: DragEvent): void {
  dragging.value = false;
  if (event.dataTransfer?.files) void addFiles(event.dataTransfer.files);
}

function dismiss(upload: Upload): void {
  uploads.value = uploads.value.filter((u) => u !== upload);
}

async function removeFile(file: FileOut): Promise<void> {
  if (!window.confirm(`Удалить «${file.filename}» из материалов?`)) return;
  try {
    await projects.removeFile(project.value.id, file.id);
    project.value.files = project.value.files.filter((f) => f.id !== file.id);
  } catch (err) {
    fileError.value = errorText(err);
  }
}

/* ------------------------------------------------------------------- text */

const textTitle = ref("");
const text = ref("");
const savingText = ref(false);
const MAX_TEXT = 200_000;

async function addText(): Promise<void> {
  savingText.value = true;
  fileError.value = null;
  try {
    const stored = await projects.addText(
      project.value.id,
      textTitle.value.trim() || "Текст",
      text.value,
    );
    project.value.files.push(stored);
    text.value = "";
    textTitle.value = "";
  } catch (err) {
    fileError.value = errorText(err);
  } finally {
    savingText.value = false;
  }
}

/* --------------------------------------------------------------- settings */

const settings = reactive({ ...project.value.settings } as DeckSettings & VideoSettings);
const settingsState = ref<"" | "saving" | "saved" | string>("");
let saveTimer: ReturnType<typeof setTimeout> | null = null;

watch(settings, () => {
  if (saveTimer !== null) clearTimeout(saveTimer);
  saveTimer = setTimeout(() => void saveSettings(), 400);
});

const SETTING_LABELS: Record<string, string> = {
  min_seconds: "длительность от",
  max_seconds: "длительность до",
  slides: "число слайдов",
  language: "язык",
  tone: "тон",
};
const labelled = (text: string) =>
  Object.entries(SETTING_LABELS).reduce((t, [field, label]) => t.replaceAll(field, label), text);

async function saveSettings(): Promise<void> {
  if (saveTimer !== null) clearTimeout(saveTimer);
  saveTimer = null;
  settingsState.value = "saving";
  try {
    const fields =
      project.value.kind === "deck"
        ? { slides: settings.slides || null, language: settings.language, tone: settings.tone }
        : {
            min_seconds: Number(settings.min_seconds),
            max_seconds: Number(settings.max_seconds),
            language: settings.language,
            tone: settings.tone,
          };
    ws.project.value = await projects.update(project.value.id, { settings: fields });
    settingsState.value = "saved";
  } catch (err) {
    settingsState.value = labelled(errorText(err));
  }
}

/* ------------------------------------------------------------------ start */

const starting = ref(false);
const startError = ref<string | null>(null);
const planning = computed(() => isActive(ws.planJob.value));

async function start(): Promise<void> {
  const question = ws.dirty.value
    ? "Составить сценарий заново? Несохранённые правки сценария пропадут, сохранённые версии останутся."
    : "Составить сценарий заново? Текущий останется в версиях.";
  if (ws.scenario.value && !window.confirm(question)) return;
  starting.value = true;
  startError.value = null;
  try {
    // A settings change still waiting for its debounce must reach the server first.
    if (saveTimer !== null || settingsState.value === "saving") await saveSettings();
    if (settingsState.value !== "saved" && settingsState.value !== "") {
      startError.value = "Сначала исправьте настройки";
      return;
    }
    await ws.startPlan();
    await router.push({ name: "project", params: { id: project.value.id, step: "scenario" } });
  } catch (err) {
    startError.value = errorText(err);
  } finally {
    starting.value = false;
  }
}
</script>

<template>
  <div class="grid-2">
    <section class="card stack">
      <h2>Материалы</h2>
      <p class="muted small">
        Таблицы и документы (.docx, .xlsx, .pptx) или текст (.txt, .md): до {{ MAX_FILES }} файлов
        по 20 МБ. Цифры на слайды попадают только из таблиц; указания модели внутри документов
        сервис не выполняет.
      </p>
      <label
        class="drop"
        :class="{ over: dragging }"
        @dragover.prevent="dragging = true"
        @dragleave="dragging = false"
        @drop.prevent="onDrop"
      >
        <input type="file" multiple :accept="ACCEPT" class="visually-hidden" @change="onPick" />
        <strong>Перетащите файлы сюда</strong>
        <span class="muted small">или нажмите, чтобы выбрать</span>
      </label>
      <p v-if="fileError" class="alert alert-bad" role="alert">{{ fileError }}</p>
      <ul v-if="uploads.length || project.files.length" class="files">
        <li v-for="u in uploads" :key="`u-${u.name}`" class="spread">
          <span>{{ u.name }}</span>
          <span v-if="u.state === 'failed'" class="row">
            <span class="alert alert-bad small">{{ u.message }}</span>
            <button type="button" class="icon" :aria-label="`Убрать ${u.name}`" @click="dismiss(u)">
              ✕
            </button>
          </span>
          <span v-else class="muted small">{{
            u.state === "uploading" ? "загружается…" : "ждёт"
          }}</span>
        </li>
        <li v-for="f in project.files" :key="f.id">
          <div class="spread">
            <span>
              <span class="badge">{{ f.kind }}</span> {{ f.filename }}
              <span class="muted small">· {{ size(f.size) }}</span>
            </span>
            <button
              type="button"
              class="icon"
              :aria-label="`Удалить ${f.filename}`"
              @click="removeFile(f)"
            >
              Удалить
            </button>
          </div>
          <p v-for="note in f.notes" :key="note" class="small muted note">{{ note }}</p>
        </li>
      </ul>
      <p v-else class="muted small">Файлов пока нет</p>

      <details>
        <summary>Вставить текст вручную</summary>
        <form class="stack text-form" @submit.prevent="addText">
          <div>
            <label for="text-title">Название</label>
            <input
              id="text-title"
              v-model="textTitle"
              type="text"
              maxlength="120"
              placeholder="Текст"
            />
          </div>
          <div>
            <label for="text-body">Текст</label>
            <textarea id="text-body" v-model="text" rows="8" :maxlength="MAX_TEXT" />
            <p class="muted small">{{ text.length.toLocaleString("ru-RU") }} из 200 000 символов</p>
          </div>
          <button type="submit" class="secondary" :disabled="savingText || !text.trim()">
            Добавить текст
          </button>
        </form>
      </details>
    </section>

    <div>
      <section class="card stack">
        <h2>Что получить</h2>
        <template v-if="project.kind === 'deck'">
          <div>
            <label for="slides">Число слайдов</label>
            <select id="slides" v-model.number="settings.slides">
              <option :value="null">Как требуют материалы (обычно 6–10)</option>
              <option v-for="n in 13" :key="n" :value="n + 2">{{ n + 2 }}</option>
            </select>
          </div>
        </template>
        <template v-else>
          <div class="row">
            <div class="grow">
              <label for="min-s">Длительность от, с</label>
              <input
                id="min-s"
                v-model.number="settings.min_seconds"
                type="number"
                min="20"
                max="300"
              />
            </div>
            <div class="grow">
              <label for="max-s">до, с</label>
              <input
                id="max-s"
                v-model.number="settings.max_seconds"
                type="number"
                min="20"
                max="300"
              />
            </div>
          </div>
        </template>
        <div class="row">
          <div class="grow">
            <label for="language">Язык</label>
            <select id="language" v-model="settings.language">
              <option value="ru">Русский</option>
              <option value="en">Английский</option>
            </select>
          </div>
          <div class="grow">
            <label for="tone">Тон</label>
            <select id="tone" v-model="settings.tone">
              <option value="business">Деловой</option>
              <option value="neutral">Нейтральный</option>
              <option value="friendly">Дружелюбный</option>
            </select>
          </div>
        </div>
        <p
          class="small"
          :class="
            settingsState === 'saved' || settingsState === 'saving' || !settingsState
              ? 'muted'
              : 'alert alert-bad'
          "
        >
          {{
            settingsState === "saving"
              ? "Сохраняем…"
              : settingsState === "saved"
                ? "Настройки сохранены"
                : settingsState
          }}
        </p>
      </section>
      <BrandPanel />
    </div>
  </div>

  <section class="card cta">
    <JobProgress
      v-if="planning && ws.planJob.value"
      :job="ws.planJob.value"
      title="Составляем сценарий"
    />
    <div v-else class="spread">
      <p class="muted">
        {{
          uploading
            ? "Дождитесь, пока загрузятся файлы."
            : project.files.length
              ? "Модель составит сценарий, а вы проверите и поправите его перед сборкой."
              : "Добавьте хотя бы один файл или текст."
        }}
      </p>
      <button
        type="button"
        :disabled="starting || uploading || !project.files.length"
        @click="start"
      >
        {{ ws.scenario.value ? "Составить сценарий заново" : "Составить сценарий" }}
      </button>
    </div>
    <p v-if="startError" class="alert alert-bad" role="alert">{{ startError }}</p>
  </section>
</template>

<style scoped>
.drop {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  padding: 24px;
  border: 2px dashed var(--line);
  border-radius: var(--radius);
  cursor: pointer;
  color: var(--text);
  font-size: 15px;
  font-weight: 400;
  text-align: center;
}
.drop.over,
.drop:hover {
  border-color: var(--accent-2);
  background: var(--accent-soft);
}
.drop:focus-within {
  outline: 2px solid var(--accent-2);
}
.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  opacity: 0;
}
.files {
  list-style: none;
  padding: 0;
  margin: 0;
}
.files li {
  padding: 8px 0;
  border-bottom: 1px solid var(--line);
}
.note {
  margin: 4px 0 0;
}
.grow {
  flex: 1;
  min-width: 140px;
}
.text-form {
  margin-top: 12px;
}
summary {
  cursor: pointer;
  font-weight: 600;
  color: var(--accent);
}
.cta {
  margin-top: 16px;
}
</style>
