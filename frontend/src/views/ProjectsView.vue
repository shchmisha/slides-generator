<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";

import { errorText } from "../api/client";
import { projects } from "../api/endpoints";
import type { Project, ProjectKind } from "../api/types";
import { when } from "../lib/format";

const router = useRouter();
const list = ref<Project[]>([]);
const loading = ref(true);
const error = ref<string | null>(null);

const title = ref("");
const kind = ref<ProjectKind>("deck");
const creating = ref(false);

const KINDS: { kind: ProjectKind; title: string; text: string }[] = [
  {
    kind: "deck",
    title: "Отчёт для правления (PPTX)",
    text: "Таблицы и комментарии → презентация с таблицей, графиком, схемой затрат и выводами",
  },
  {
    kind: "video",
    title: "Ролик (MP4)",
    text: "Текст о продукте → ролик на 60–75 секунд: сцены с анимацией и субтитрами",
  },
];

async function refresh(): Promise<void> {
  loading.value = true;
  try {
    list.value = await projects.list();
    error.value = null;
  } catch (err) {
    error.value = errorText(err);
  } finally {
    loading.value = false;
  }
}

async function create(): Promise<void> {
  creating.value = true;
  error.value = null;
  try {
    const project = await projects.create(title.value.trim(), kind.value);
    await router.push({ name: "project", params: { id: project.id, step: "materials" } });
  } catch (err) {
    error.value = errorText(err);
  } finally {
    creating.value = false;
  }
}

async function remove(project: Project): Promise<void> {
  if (!window.confirm(`Удалить проект «${project.title}» со всеми файлами?`)) return;
  try {
    await projects.remove(project.id);
    list.value = list.value.filter((p) => p.id !== project.id);
  } catch (err) {
    error.value = errorText(err);
  }
}

onMounted(refresh);
</script>

<template>
  <section class="card">
    <h1>Новый проект</h1>
    <form class="stack" @submit.prevent="create">
      <div class="kinds" role="radiogroup" aria-label="Что сделать">
        <label v-for="k in KINDS" :key="k.kind" class="kind" :class="{ chosen: kind === k.kind }">
          <input v-model="kind" type="radio" name="kind" :value="k.kind" />
          <strong>{{ k.title }}</strong>
          <span class="muted small">{{ k.text }}</span>
        </label>
      </div>
      <div>
        <label for="title">Название</label>
        <input
          id="title"
          v-model="title"
          type="text"
          maxlength="200"
          placeholder="Например: Отчёт за 3 квартал"
          required
        />
      </div>
      <button type="submit" :disabled="creating || !title.trim()">Создать</button>
    </form>
  </section>

  <section class="card">
    <h2>Мои проекты</h2>
    <p v-if="error" class="alert alert-bad" role="alert">{{ error }}</p>
    <p v-if="loading" class="muted">Загружаем…</p>
    <p v-else-if="!list.length" class="empty">Проектов пока нет — создайте первый выше</p>
    <ul v-else class="projects">
      <li v-for="p in list" :key="p.id" class="spread">
        <RouterLink :to="{ name: 'project', params: { id: p.id } }" class="project-link">
          <strong>{{ p.title }}</strong>
          <span class="muted small">
            {{ p.kind === "deck" ? "Презентация" : "Ролик" }} · файлов: {{ p.files.length }} ·
            изменён {{ when(p.updated_at) }}
          </span>
        </RouterLink>
        <button
          type="button"
          class="danger"
          :aria-label="`Удалить проект «${p.title}»`"
          @click="remove(p)"
        >
          Удалить
        </button>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.kinds {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 12px;
}
.kind {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 14px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  cursor: pointer;
  color: var(--text);
  font-size: 15px;
  font-weight: 400;
  margin: 0;
}
.kind input {
  position: absolute;
  opacity: 0;
}
.kind.chosen {
  border-color: var(--accent);
  background: var(--accent-soft);
}
.kind:focus-within {
  outline: 2px solid var(--accent-2);
}
.projects {
  list-style: none;
  padding: 0;
  margin: 0;
}
.projects li {
  padding: 12px 0;
  border-bottom: 1px solid var(--line);
}
.project-link {
  display: flex;
  flex-direction: column;
  text-decoration: none;
  color: var(--text);
}
</style>
