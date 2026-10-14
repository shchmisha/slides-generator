<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";

import { isActive, type Job } from "../api/types";
import { elapsed, seconds } from "../lib/format";

const props = defineProps<{ job: Job; title: string }>();

// Ticks once a second so the elapsed time moves between polls.
const now = ref(Date.now());
let timer: ReturnType<typeof setInterval> | null = null;
onMounted(() => {
  timer = setInterval(() => (now.value = Date.now()), 1000);
});
onBeforeUnmount(() => {
  if (timer !== null) clearInterval(timer);
});

const spent = computed(() => {
  void now.value;
  return elapsed(props.job.created_at, isActive(props.job) ? null : props.job.finished_at);
});
</script>

<template>
  <div class="job" role="status" aria-live="polite">
    <div class="spread">
      <strong>{{ title }}</strong>
      <span class="muted small" aria-hidden="true">{{ seconds(spent) }}</span>
    </div>
    <div
      class="bar"
      role="progressbar"
      :aria-label="title"
      :aria-valuenow="job.progress"
      aria-valuemin="0"
      aria-valuemax="100"
    >
      <div class="fill" :class="job.status" :style="{ width: `${Math.max(job.progress, 3)}%` }" />
    </div>
    <p class="muted small stage">{{ job.stage }}</p>
    <p v-if="job.status === 'failed' && job.error" class="alert alert-bad">{{ job.error }}</p>
  </div>
</template>

<style scoped>
.bar {
  height: 8px;
  border-radius: 999px;
  background: var(--surface-2);
  border: 1px solid var(--line);
  overflow: hidden;
  margin: 8px 0 4px;
}
.fill {
  height: 100%;
  background: var(--accent-2);
  transition: width 0.4s ease;
}
.fill.queued {
  background: var(--muted);
}
.fill.succeeded {
  background: var(--ok);
}
.fill.failed {
  background: var(--bad);
}
.stage::first-letter {
  text-transform: uppercase;
}
</style>
