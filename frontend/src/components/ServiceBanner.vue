<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";

import { fetchWorkers } from "../api/health";

/** Warn before a job would wait forever: the API answers, but no worker takes jobs. */
const missing = ref<string[]>([]);
let timer: ReturnType<typeof setInterval> | null = null;

async function check(): Promise<void> {
  try {
    const workers = await fetchWorkers();
    if (!workers) {
      missing.value = []; // unknown, not "down": no banner rather than a wrong one
      return;
    }
    const queues = workers.queues;
    missing.value = [
      ...((queues.ai ?? 0) > 0 ? [] : ["составления сценариев"]),
      ...((queues.render ?? 0) > 0 ? [] : ["сборки файлов"]),
    ];
  } catch {
    missing.value = [];
  }
}

onMounted(() => {
  void check();
  timer = setInterval(() => void check(), 30_000);
});
onBeforeUnmount(() => {
  if (timer !== null) clearInterval(timer);
});
</script>

<template>
  <div v-if="missing.length" class="banner alert alert-warn" role="status">
    Сейчас не запущен воркер {{ missing.join(" и ") }}: задачи подождут в очереди и начнутся, как
    только он заработает.
  </div>
</template>

<style scoped>
.banner {
  border-radius: 0;
  text-align: center;
}
</style>
