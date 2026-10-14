<script setup lang="ts">
import { computed } from "vue";

import type { DeckReport, VideoReport } from "../api/types";

/** What the service checked and what it wants the user to look at before building. */
const props = defineProps<{ report: DeckReport | VideoReport }>();

const removed = computed(() => props.report.notes.filter((n) => n.kind === "quarantine"));
const data = computed(() => props.report.notes.filter((n) => n.kind !== "quarantine"));
const claims = computed(() => ("claims" in props.report ? (props.report.claims ?? []) : []));
</script>

<template>
  <section class="card stack">
    <h2>Проверка</h2>
    <div v-if="removed.length" class="alert alert-bad">
      В материалах были указания для модели — сервис убрал их и не выполнял:
      <ul>
        <li v-for="n in removed" :key="n.message">{{ n.source }}: {{ n.message }}</li>
      </ul>
    </div>
    <div v-if="report.warnings.length" class="alert alert-warn">
      Стоит посмотреть:
      <ul>
        <li v-for="w in report.warnings" :key="w">{{ w }}</li>
      </ul>
    </div>
    <div v-if="claims.length">
      <h3>Цифры из документов сверены с таблицами</h3>
      <div class="table-wrap">
        <table class="data">
          <thead>
            <tr>
              <th>В документе</th>
              <th>По таблице</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in claims" :key="c.quote">
              <td>«{{ c.quote }}»</td>
              <td>
                {{ c.label }}: <strong>{{ c.value }}</strong>
              </td>
              <td>
                <span v-if="c.matches === false" class="badge badge-bad"
                  >расходится — на слайдах цифра из таблицы</span
                >
                <span v-else-if="c.matches" class="badge badge-ok">совпадает</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
    <details v-if="data.length">
      <summary>Что сервис исправил и чего не хватало в данных ({{ data.length }})</summary>
      <ul class="small">
        <li v-for="n in data" :key="`${n.source}${n.location}${n.message}`">
          <span class="muted"
            >{{ n.source }}<template v-if="n.location"> · {{ n.location }}</template
            >:</span
          >
          {{ n.message }}
        </li>
      </ul>
    </details>
    <p
      v-if="!removed.length && !report.warnings.length && !claims.length && !data.length"
      class="muted"
    >
      Замечаний нет
    </p>
  </section>
</template>

<style scoped>
summary {
  cursor: pointer;
  font-weight: 600;
  color: var(--accent);
}
</style>
