<script setup lang="ts">
import { computed, onBeforeUnmount, ref, useId } from "vue";

import { useEditing } from "../composables/editing";
import { segments } from "../lib/placeholders";

/**
 * A text of the scenario. Figures are placeholders ("{rev_fact}"); under the field they are
 * shown with the value from the data. Focusing the field makes it the target of the value
 * picker.
 */
const model = defineModel<string | undefined>({ required: true });
const props = withDefaults(
  defineProps<{
    label: string;
    max?: number;
    rows?: number;
    optional?: boolean;
    invalid?: boolean;
  }>(),
  { max: 320, rows: 1, optional: false, invalid: false },
);

const id = useId();
const editing = useEditing();
const element = ref<HTMLTextAreaElement | null>(null);
const parts = computed(() => segments(model.value ?? "", editing.values.value));
const hasValues = computed(() => parts.value.some((p) => p.kind === "value"));

function write(value: string): void {
  model.value = value === "" && props.optional ? undefined : value;
}

function onFocus(): void {
  if (element.value) editing.target.value = { element: element.value, write };
}

onBeforeUnmount(() => {
  if (editing.target.value?.element === element.value) editing.target.value = null;
});
</script>

<template>
  <div class="text-field">
    <label :for="id">{{ label }}<span v-if="optional" class="muted"> (необязательно)</span></label>
    <textarea
      :id="id"
      ref="element"
      :value="model ?? ''"
      :rows="rows"
      :maxlength="max"
      :class="{ 'field-error': invalid }"
      @focus="onFocus"
      @input="write(($event.target as HTMLTextAreaElement).value)"
    />
    <p v-if="hasValues" class="resolved small">
      <template v-for="(part, i) in parts" :key="i">
        <span v-if="part.kind === 'text'">{{ part.text }}</span>
        <span
          v-else
          class="chip"
          :class="{ missing: part.shown === null }"
          :title="part.label ?? `Нет значения ${part.token}`"
          >{{ part.shown ?? part.token }}</span
        >
      </template>
    </p>
  </div>
</template>

<style scoped>
.text-field + .text-field {
  margin-top: 10px;
}
textarea {
  min-height: 0;
}
.resolved {
  margin: 4px 0 0;
  color: var(--muted);
}
.chip {
  display: inline-block;
  padding: 0 6px;
  border-radius: 6px;
  background: var(--accent-soft);
  color: var(--accent);
  font-weight: 600;
}
.chip.missing {
  background: var(--bad-soft);
  color: var(--bad);
}
</style>
