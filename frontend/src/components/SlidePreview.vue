<script setup lang="ts">
import type { SpecSlide } from "../api/types";

/** The slide as it will be built, with every figure filled in from the data. */
defineProps<{ slide: SpecSlide }>();

const number = (value: number | null) =>
  value === null ? "нет данных" : value.toLocaleString("ru-RU", { maximumFractionDigits: 1 });
</script>

<template>
  <div class="preview">
    <p class="title">{{ slide.title }}</p>
    <p v-if="slide.subtitle" class="muted small">{{ slide.subtitle }}</p>

    <div v-if="slide.kpis" class="kpis">
      <div v-for="(k, i) in slide.kpis" :key="i" class="kpi">
        <strong>{{ k.value }}</strong> <span class="muted small">{{ k.unit }}</span>
        <div class="small">{{ k.label }}</div>
        <div v-if="k.delta" class="small" :class="k.tone">{{ k.delta }}</div>
      </div>
    </div>
    <ol v-if="slide.takeaways" class="small">
      <li v-for="(t, i) in slide.takeaways" :key="i">{{ t }}</li>
    </ol>

    <div v-if="slide.chart" class="block">
      <p class="muted small">График{{ slide.chart.caption ? `: ${slide.chart.caption}` : "" }}</p>
      <table class="mini">
        <thead>
          <tr>
            <th></th>
            <th v-for="s in slide.chart.series" :key="s.name" class="num">{{ s.name }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(c, i) in slide.chart.categories" :key="c">
            <td>{{ c }}</td>
            <td v-for="s in slide.chart.series" :key="s.name" class="num">
              {{ number(s.values[i] ?? null) }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="slide.table" class="block">
      <p class="muted small">Таблица</p>
      <table class="mini">
        <thead>
          <tr>
            <th
              v-for="(c, i) in slide.table.columns"
              :key="i"
              :class="{ num: c.align === 'right' }"
            >
              {{ c.header }}
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, r) in slide.table.rows" :key="r">
            <td
              v-for="(cell, i) in row"
              :key="i"
              :class="[cell.tone, { num: slide.table.columns[i]?.align === 'right', b: cell.bold }]"
            >
              {{ cell.text }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="slide.items" class="block">
      <p v-if="slide.total" class="small">
        {{ slide.total.label }}: <strong>{{ slide.total.value }}</strong>
      </p>
      <p v-if="slide.changeLabel" class="muted small">{{ slide.changeLabel }}</p>
      <ul class="small">
        <li v-for="item in slide.items" :key="item.label">
          {{ item.label }} — {{ item.value }} ({{ item.shareText }})
          <span v-if="item.change">, {{ item.change }}</span>
        </li>
      </ul>
    </div>

    <div v-if="slide.risks" class="grid-2 block">
      <div>
        <p class="muted small">Риски</p>
        <p v-for="p in slide.risks" :key="p.title" class="small">
          <strong>{{ p.title }}.</strong> {{ p.text }}
        </p>
      </div>
      <div>
        <p class="muted small">Решения</p>
        <p v-for="p in slide.decisions" :key="p.title" class="small">
          <strong>{{ p.title }}.</strong> {{ p.text }}
        </p>
      </div>
    </div>

    <p v-if="slide.callout" class="callout small">{{ slide.callout }}</p>
  </div>
</template>

<style scoped>
.preview {
  background: var(--surface-2);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 12px;
}
.title {
  font-weight: 700;
  margin-bottom: 4px;
}
.kpis {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
  gap: 8px;
  margin: 8px 0;
}
.kpi {
  background: var(--surface);
  border-radius: 6px;
  padding: 8px;
}
.block {
  margin-top: 8px;
}
.block {
  overflow-x: auto;
}
.mini {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.mini th,
.mini td {
  padding: 3px 6px;
  border-bottom: 1px solid var(--line);
  text-align: left;
}
.positive {
  color: var(--ok);
}
.negative {
  color: var(--bad);
}
.b {
  font-weight: 700;
}
.callout {
  margin-top: 8px;
  padding: 6px 10px;
  border-left: 3px solid var(--warn);
  background: var(--surface);
}
.grid-2 {
  gap: 10px;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
}
</style>
