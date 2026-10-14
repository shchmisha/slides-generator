<script setup lang="ts">
import { computed } from "vue";

import type { DeckContent, SlideContent, SpecSlide } from "../api/types";
import { useEditing } from "../composables/editing";
import { fieldOf, SLIDE_TYPES, shortPath } from "../lib/scenario";
import SlidePreview from "./SlidePreview.vue";
import TextField from "./TextField.vue";

const props = defineProps<{
  index: number;
  count: number;
  preview: SpecSlide | null;
  stale: boolean;
  errors: string[];
  /** A fixed slide count was asked for: deleting would make the scenario invalid. */
  fixedCount: boolean;
}>();
const emit = defineEmits<{ move: [to: number]; remove: [] }>();

const editing = useEditing();
const slide = computed(
  () => (editing.draft.value as DeckContent).slides[props.index] as unknown as SlideContent,
);
/** Exactly this field (not one of its children) has an error. */
const has = (field: string) => props.errors.some((e) => fieldOf(e) === field);

function addItem(
  list: string[] | { title: string; text: string }[],
  max: number,
  item: unknown,
): void {
  if (list.length < max) (list as unknown[]).push(item);
}
function removeItem(list: unknown[], i: number, min: number): void {
  if (list.length > min) list.splice(i, 1);
}
</script>

<template>
  <article class="card slide-card">
    <header class="spread card-head">
      <h3>{{ index + 1 }}. {{ SLIDE_TYPES[slide.type] ?? slide.type }}</h3>
      <div class="row">
        <button
          type="button"
          class="icon"
          :disabled="index === 0"
          aria-label="Выше"
          @click="emit('move', index - 1)"
        >
          ↑
        </button>
        <button
          type="button"
          class="icon"
          :disabled="index === count - 1"
          aria-label="Ниже"
          @click="emit('move', index + 1)"
        >
          ↓
        </button>
        <button
          type="button"
          class="icon"
          :disabled="count === 1 || fixedCount"
          :title="fixedCount ? 'В настройках задано точное число слайдов' : ''"
          :aria-label="`Удалить слайд ${index + 1}`"
          @click="emit('remove')"
        >
          Удалить
        </button>
      </div>
    </header>

    <div v-if="errors.length" class="alert alert-bad small" role="alert">
      <ul>
        <li v-for="e in errors" :key="e">{{ shortPath(e) }}</li>
      </ul>
    </div>

    <div class="body">
      <div class="fields">
        <TextField
          v-model="slide.title"
          label="Заголовок"
          :max="120"
          :rows="2"
          :invalid="has('title')"
        />
        <TextField
          v-if="slide.type !== 'data_notes'"
          v-model="slide.subtitle"
          :invalid="has('subtitle')"
          label="Подзаголовок"
          :max="120"
          optional
        />

        <template v-if="slide.kpis">
          <p class="group">Показатели</p>
          <div v-for="(k, i) in slide.kpis" :key="`k${i}`" class="sub">
            <TextField
              v-model="k.label"
              :invalid="has(`kpis[${i}].label`)"
              :label="`Показатель ${i + 1}: подпись`"
              :max="120"
            />
            <TextField
              v-model="k.delta"
              :invalid="has(`kpis[${i}].delta`)"
              label="Пояснение под цифрой"
              optional
            />
          </div>
        </template>

        <template v-if="slide.takeaways">
          <p class="group">Выводы</p>
          <div v-for="(_, i) in slide.takeaways" :key="`t${i}`" class="sub row-item">
            <TextField
              v-model="slide.takeaways[i]"
              :label="`Вывод ${i + 1}`"
              :rows="2"
              :invalid="has(`takeaways[${i}]`)"
            />
            <button
              type="button"
              class="icon"
              :disabled="slide.takeaways.length <= 2"
              :aria-label="`Удалить вывод ${i + 1}`"
              @click="removeItem(slide.takeaways, i, 2)"
            >
              ✕
            </button>
          </div>
          <button
            type="button"
            class="secondary small-button"
            :disabled="slide.takeaways.length >= 5"
            @click="addItem(slide.takeaways, 5, 'Новый вывод')"
          >
            + Вывод
          </button>
        </template>

        <template v-if="slide.chart">
          <p class="group">График</p>
          <TextField
            v-model="slide.chart.caption"
            :invalid="has('chart.caption')"
            label="Подпись графика"
            :max="120"
            optional
          />
          <TextField
            v-for="(s, i) in slide.chart.series ?? []"
            :key="`s${i}`"
            v-model="s.name"
            :invalid="has(`chart.series[${i}].name`)"
            :label="`Ряд ${i + 1}: название в легенде`"
            :max="120"
          />
        </template>

        <template v-if="slide.table">
          <p class="group">Таблица</p>
          <TextField
            v-model="slide.table.row_header"
            :invalid="has('table.row_header')"
            label="Заголовок первого столбца"
            :max="120"
          />
          <TextField
            v-for="(c, i) in slide.table.columns ?? []"
            :key="`c${i}`"
            v-model="c.header"
            :invalid="has(`table.columns[${i}].header`)"
            :label="`Столбец ${i + 1}`"
            :max="120"
          />
          <TextField
            v-model="slide.table.total_label"
            :invalid="has('table.total_label')"
            label="Строка итога"
            :max="120"
            optional
          />
        </template>

        <TextField
          v-if="slide.type === 'cost_structure'"
          v-model="slide.total_label"
          :invalid="has('total_label')"
          label="Подпись итога"
          :max="120"
          optional
        />

        <template v-for="side in ['risks', 'decisions'] as const" :key="side">
          <template v-if="slide[side]">
            <p class="group">{{ side === "risks" ? "Риски" : "Решения" }}</p>
            <div v-for="(p, i) in slide[side]" :key="`${side}${i}`" class="sub">
              <div class="row-item">
                <TextField
                  v-model="p.title"
                  :invalid="has(`${side}[${i}].title`)"
                  :label="`${i + 1}. Кратко`"
                  :max="120"
                />
                <button
                  type="button"
                  class="icon"
                  :disabled="(slide[side]?.length ?? 0) <= 1"
                  :aria-label="`Удалить ${side === 'risks' ? 'риск' : 'решение'} ${i + 1}`"
                  @click="removeItem(slide[side] ?? [], i, 1)"
                >
                  ✕
                </button>
              </div>
              <TextField
                v-model="p.text"
                :invalid="has(`${side}[${i}].text`)"
                label="Подробнее"
                :rows="2"
              />
            </div>
            <button
              type="button"
              class="secondary small-button"
              :disabled="(slide[side]?.length ?? 0) >= 4"
              @click="addItem(slide[side] ?? [], 4, { title: 'Кратко', text: 'Подробнее' })"
            >
              + {{ side === "risks" ? "Риск" : "Решение" }}
            </button>
          </template>
        </template>

        <TextField
          v-if="
            'callout' in slide ||
            ['chart_table', 'chart', 'table', 'cost_structure'].includes(slide.type)
          "
          v-model="slide.callout"
          label="Вывод внизу слайда"
          :rows="2"
          optional
          :invalid="has('callout')"
        />
        <p v-if="slide.type === 'data_notes'" class="muted small">
          Таблицу проверки данных заполняет сервис: исправления, пропуски и сверку утверждений из
          документов с таблицами.
        </p>
      </div>

      <div class="side">
        <p class="muted small">Как будет на слайде</p>
        <p v-if="stale" class="alert alert-info small">Предпросмотр обновится после сохранения</p>
        <SlidePreview v-if="preview" :slide="preview" :class="{ faded: stale }" />
        <p v-else-if="!stale" class="muted small">
          Этот слайд не попадёт в презентацию: проверять нечего
        </p>
      </div>
    </div>
  </article>
</template>

<style scoped>
.slide-card h3 {
  margin: 0;
}
.body {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 16px;
  margin-top: 12px;
}
@media (max-width: 900px) {
  .body {
    grid-template-columns: minmax(0, 1fr);
  }
}
.group {
  margin: 14px 0 6px;
  font-weight: 700;
  font-size: 13px;
  color: var(--accent);
}
.sub {
  padding-left: 10px;
  border-left: 2px solid var(--line);
  margin-bottom: 8px;
}
.row-item {
  display: flex;
  align-items: flex-end;
  gap: 6px;
}
.row-item > :first-child {
  flex: 1;
}
.small-button {
  padding: 4px 10px;
  font-size: 13px;
}
.faded {
  opacity: 0.5;
}
</style>
