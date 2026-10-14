<script setup lang="ts">
import { computed, nextTick, ref } from "vue";

import { useEditing } from "../composables/editing";
import { insertAt, token } from "../lib/placeholders";

/** Every figure the scenario may use: inserting one adds a placeholder, never a typed number. */
const editing = useEditing();
const query = ref("");
const open = ref<string | null>(null);
const hint = ref<string | null>(null);
/** On narrow screens the panel floats and opens from a button, so it is always one tap away. */
const shown = ref(false);

const list = computed(() => {
  const q = query.value.trim().toLowerCase();
  return editing.values.value.filter(
    (v) =>
      !q ||
      v.label.toLowerCase().includes(q) ||
      v.name.includes(q) ||
      v.items?.some((i) => i.key.toLowerCase().includes(q)),
  );
});

/** "Выручка по филиалам, 3 квартал › Итого по компании › Итого факт": table, then the rest. */
function parts(label: string): { table: string; item: string } {
  const [table = "", ...rest] = label.split(" › ");
  return rest.length ? { table, item: rest.join(" › ") } : { table: "", item: table };
}

async function insert(name: string, key?: string): Promise<void> {
  const target = editing.target.value;
  if (!target || !target.element.isConnected) {
    hint.value = "Сначала поставьте курсор в текст, куда вставить значение";
    return;
  }
  hint.value = null;
  const el = target.element;
  const start = el.selectionStart ?? el.value.length;
  const end = el.selectionEnd ?? start;
  const result = insertAt(el.value, start, end, token(name, key));
  target.write(result.value);
  shown.value = false;
  await nextTick();
  el.focus();
  el.setSelectionRange(result.cursor, result.cursor);
}
</script>

<template>
  <button
    type="button"
    class="picker-toggle"
    :aria-expanded="shown"
    aria-controls="value-picker"
    @mousedown.prevent
    @click="shown = !shown"
  >
    {{ shown ? "Закрыть" : "Вставить значение" }}
  </button>
  <aside id="value-picker" class="picker card" :class="{ shown }">
    <h3>Значения из данных</h3>
    <p class="muted small">
      Поставьте курсор в текст и выберите значение: на слайд попадёт цифра из таблицы.
    </p>
    <input
      v-model="query"
      type="text"
      placeholder="Поиск: выручка, Урал…"
      aria-label="Поиск значения"
    />
    <p v-if="hint" class="alert alert-info small">{{ hint }}</p>
    <ul class="values">
      <li v-for="v in list" :key="v.name">
        <button
          v-if="v.value !== undefined"
          type="button"
          class="value"
          :title="v.label"
          @mousedown.prevent
          @click="insert(v.name)"
        >
          <span class="label">
            <span class="item-name">{{ parts(v.label).item }}</span>
            <span v-if="parts(v.label).table" class="table-name">{{ parts(v.label).table }}</span>
          </span>
          <strong>{{ v.value }}</strong>
        </button>
        <template v-else>
          <button
            type="button"
            class="value"
            :aria-expanded="open === v.name"
            @mousedown.prevent
            @click="open = open === v.name ? null : v.name"
          >
            <span class="label">
              <span class="item-name">{{ parts(v.label).item }}</span>
              <span v-if="parts(v.label).table" class="table-name">{{ parts(v.label).table }}</span>
            </span>
            <span class="muted small keys">
              {{
                v.items
                  ?.slice(0, 2)
                  .map((i) => i.key)
                  .join(", ")
              }}{{ (v.items?.length ?? 0) > 2 ? ` и ещё ${(v.items?.length ?? 0) - 2}` : "" }}
              ▾
            </span>
          </button>
          <ul v-if="open === v.name" class="items">
            <li v-for="item in v.items" :key="item.key">
              <button
                type="button"
                class="value item"
                @mousedown.prevent
                @click="insert(v.name, item.key)"
              >
                <span class="label">{{ item.key }}</span>
                <strong>{{ item.value }}</strong>
              </button>
            </li>
          </ul>
        </template>
      </li>
    </ul>
  </aside>
</template>

<style scoped>
.picker {
  position: sticky;
  top: var(--toolbar-h, 16px);
  max-height: calc(100vh - var(--toolbar-h, 16px) - 16px);
  overflow: auto;
}
.picker-toggle {
  display: none;
}
@media (max-width: 1100px) {
  .picker-toggle {
    display: inline-flex;
    position: fixed;
    right: 16px;
    bottom: 16px;
    z-index: 5;
    box-shadow: var(--shadow);
  }
  .picker {
    display: none;
    position: fixed;
    right: 16px;
    bottom: 64px;
    z-index: 5;
    width: min(380px, calc(100vw - 32px));
    max-height: 60vh;
  }
  .picker.shown {
    display: block;
  }
}
.keys {
  text-align: right;
  white-space: nowrap;
}
.values,
.items {
  list-style: none;
  padding: 0;
  margin: 10px 0 0;
}
.items {
  margin: 0 0 6px 12px;
}
.value {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 8px;
  width: 100%;
  padding: 6px 8px;
  margin-bottom: 2px;
  background: transparent;
  color: var(--text);
  font-weight: 400;
  text-align: left;
  border: 1px solid transparent;
}
.value:hover:not(:disabled) {
  background: var(--accent-soft);
}
.value .label {
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
}
.item-name {
  display: block;
  font-weight: 600;
}
.table-name {
  display: block;
  font-size: 11px;
  color: var(--muted);
}
.value strong {
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
</style>
