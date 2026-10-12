<script setup lang="ts">
import { onMounted, ref } from "vue";
import { fetchHealth, fetchWorkers, type Health, type Workers } from "./api/health";

const health = ref<Health | null>(null);
const workers = ref<Workers | null>(null);
const error = ref<string | null>(null);
const loading = ref(true);

async function refresh(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    [health.value, workers.value] = await Promise.all([fetchHealth(), fetchWorkers()]);
  } catch (err) {
    error.value = err instanceof Error ? err.message : "Сервис недоступен";
  } finally {
    loading.value = false;
  }
}

onMounted(refresh);
</script>

<template>
  <header class="topbar">
    <span class="brand">Генератор презентаций и роликов</span>
  </header>

  <main class="page">
    <section class="card" aria-live="polite">
      <h1>Состояние сервиса</h1>
      <p v-if="loading">Проверяем…</p>
      <p v-else-if="error" class="fail">{{ error }}</p>
      <ul v-else class="checks">
        <li :class="health?.database ?? 'fail'">База данных</li>
        <li :class="health?.redis ?? 'fail'">Очередь (Redis)</li>
        <li :class="(workers?.queues.ai ?? 0) > 0 ? 'ok' : 'fail'">ИИ-воркер</li>
        <li :class="(workers?.queues.render ?? 0) > 0 ? 'ok' : 'fail'">Воркер рендера</li>
      </ul>
      <button type="button" :disabled="loading" @click="refresh">Обновить</button>
    </section>
  </main>
</template>

<style>
:root {
  --bg: #f6f7f9;
  --surface: #ffffff;
  --text: #1d1d1f;
  --muted: #6b7280;
  --ok: #2f7d4f;
  --fail: #b3352b;
  --accent: #1f3a5f;
  font-family:
    system-ui,
    -apple-system,
    "Segoe UI",
    Roboto,
    Arial,
    sans-serif;
  color: var(--text);
  background: var(--bg);
}
body {
  margin: 0;
}
.topbar {
  padding: 14px 24px;
  background: var(--accent);
  color: #fff;
  font-weight: 600;
}
.page {
  max-width: 720px;
  margin: 32px auto;
  padding: 0 16px;
}
.card {
  background: var(--surface);
  border-radius: 12px;
  padding: 24px;
  box-shadow: 0 1px 3px rgb(0 0 0 / 8%);
}
h1 {
  margin-top: 0;
  font-size: 20px;
}
.checks {
  list-style: none;
  padding: 0;
}
.checks li {
  padding: 6px 0;
}
.checks li::before {
  content: "●";
  margin-right: 8px;
}
.ok::before {
  color: var(--ok);
}
.fail,
.fail::before {
  color: var(--fail);
}
button {
  padding: 8px 16px;
  border: 0;
  border-radius: 8px;
  background: var(--accent);
  color: #fff;
  cursor: pointer;
}
button:disabled {
  opacity: 0.6;
  cursor: default;
}
</style>
