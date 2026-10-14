<script setup lang="ts">
import { computed, reactive, ref } from "vue";

import { errorText } from "../api/client";
import { brand } from "../api/endpoints";
import type { ColorToken, Theme } from "../api/types";
import { useWorkspace } from "../composables/workspace";

const ws = useWorkspace();
const project = computed(() => ws.project.value!);

/** The server's default theme (backend/app/brand.py), shown until a brand is set. */
const DEFAULT: Theme = {
  name: "default",
  colors: {
    primary: "#2B4C7E",
    accent: "#D9822B",
    text: "#1F2328",
    muted: "#5E6A78",
    positive: "#2F7D4F",
    negative: "#B3352B",
    surface: "#F3F5F8",
    grid: "#D7DCE3",
    background: "#FFFFFF",
  },
  fonts: { heading: "Arial", body: "Arial" },
};

const EDITABLE: { token: ColorToken; title: string }[] = [
  { token: "primary", title: "Основной" },
  { token: "accent", title: "Акцент" },
  { token: "text", title: "Текст" },
  { token: "background", title: "Фон" },
  { token: "positive", title: "Рост, хорошо" },
  { token: "negative", title: "Падение, плохо" },
];
// Postgres JSONB returns object keys in its own order: show the palette in a fixed one.
const PALETTE: ColorToken[] = [
  "primary",
  "accent",
  "text",
  "muted",
  "background",
  "surface",
  "grid",
  "positive",
  "negative",
];
// Fonts the server can measure exactly (same metrics); others work but may wrap differently.
const MEASURED_FONTS = ["Arial", "Helvetica", "Georgia", "Times New Roman", "Courier New"];

const theme = computed<Theme>(() => project.value.brand ?? DEFAULT);
const form = reactive({
  colors: { ...theme.value.colors } as Record<ColorToken, string>,
  heading: theme.value.fonts.heading,
  body: theme.value.fonts.body,
});
const busy = ref(false);
const message = ref<{ kind: "ok" | "bad" | "warn"; text: string; list?: string[] } | null>(null);
const logoVersion = ref(project.value.updated_at);

function fill(from: Theme): void {
  Object.assign(form.colors, from.colors);
  form.heading = from.fonts.heading;
  form.body = from.fonts.body;
}

async function save(value: Record<string, unknown>): Promise<void> {
  busy.value = true;
  message.value = null;
  try {
    const result = await brand.set(project.value.id, value);
    project.value.brand = result.theme;
    fill(result.theme);
    message.value = result.warnings.length
      ? { kind: "warn", text: "Оформление сохранено, но есть замечания:", list: result.warnings }
      : { kind: "ok", text: "Оформление сохранено" };
  } catch (err) {
    message.value = { kind: "bad", text: named(errorText(err)) };
  } finally {
    busy.value = false;
  }
}

// The server mixes muted, surface and grid from primary, text and background. Keep the
// current ones (e.g. from an uploaded brand.json) unless one of those three changed.
const BASE: ColorToken[] = ["primary", "text", "background"];

function saveForm(): void {
  const baseChanged = BASE.some(
    (t) => form.colors[t].toUpperCase() !== theme.value.colors[t].toUpperCase(),
  );
  const tokens = baseChanged ? EDITABLE.map((e) => e.token) : PALETTE;
  const colors = Object.fromEntries(tokens.map((t) => [t, form.colors[t]]));
  const company = project.value.brand ? { company: project.value.brand.name } : {};
  void save({ ...company, colors, fonts: { heading: form.heading, body: form.body } });
}

const FIELD_NAMES: Record<string, string> = {
  "colors.primary": "основной цвет",
  "colors.accent": "акцентный цвет",
  "colors.text": "цвет текста",
  "colors.background": "цвет фона",
  "colors.positive": "цвет роста",
  "colors.negative": "цвет падения",
  "fonts.heading": "шрифт заголовков",
  "fonts.body": "шрифт текста",
};
function named(text: string): string {
  return Object.entries(FIELD_NAMES).reduce((t, [field, name]) => t.replaceAll(field, name), text);
}

async function loadBrandFile(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = "";
  if (!file) return;
  try {
    const parsed: unknown = JSON.parse(await file.text());
    if (!parsed || typeof parsed !== "object") throw new Error("not an object");
    await save(parsed as Record<string, unknown>);
  } catch (err) {
    message.value = {
      kind: "bad",
      text: err instanceof SyntaxError ? "Это не JSON: проверьте файл brand.json" : errorText(err),
    };
  }
}

async function reset(): Promise<void> {
  if (!window.confirm("Вернуть стандартное оформление? Фирменные цвета и шрифты будут удалены.")) {
    return;
  }
  busy.value = true;
  try {
    await brand.reset(project.value.id);
    project.value.brand = null;
    fill(DEFAULT);
    message.value = { kind: "ok", text: "Вернули стандартное оформление" };
  } catch (err) {
    message.value = { kind: "bad", text: errorText(err) };
  } finally {
    busy.value = false;
  }
}

async function uploadLogo(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = "";
  if (!file) return;
  busy.value = true;
  message.value = null;
  try {
    await brand.setLogo(project.value.id, file);
    project.value.has_logo = true;
    logoVersion.value = String(Date.now());
    message.value = { kind: "ok", text: "Логотип сохранён" };
  } catch (err) {
    message.value = { kind: "bad", text: errorText(err) };
  } finally {
    busy.value = false;
  }
}

async function removeLogo(): Promise<void> {
  if (!window.confirm("Убрать логотип со слайдов?")) return;
  try {
    await brand.removeLogo(project.value.id);
    project.value.has_logo = false;
    message.value = { kind: "ok", text: "Логотип убран" };
  } catch (err) {
    message.value = { kind: "bad", text: errorText(err) };
  }
}
</script>

<template>
  <section class="card stack">
    <div class="spread">
      <h2>Оформление</h2>
      <span class="badge">{{ project.brand ? "фирменное" : "стандартное" }}</span>
    </div>
    <div class="swatches" aria-label="Цвета темы">
      <span
        v-for="token in PALETTE"
        :key="token"
        class="swatch"
        :style="{ background: theme.colors[token] }"
        :title="`${token}: ${theme.colors[token]}`"
      />
    </div>

    <div class="row">
      <label class="button secondary file-button">
        Загрузить brand.json
        <input
          type="file"
          accept=".json,application/json"
          class="visually-hidden"
          @change="loadBrandFile"
        />
      </label>
      <button v-if="project.brand" type="button" class="icon" :disabled="busy" @click="reset">
        Сбросить
      </button>
    </div>

    <details>
      <summary>Настроить цвета и шрифты</summary>
      <form class="stack form" @submit.prevent="saveForm">
        <div class="colors">
          <div v-for="c in EDITABLE" :key="c.token" class="color">
            <label :for="`c-${c.token}`">{{ c.title }}</label>
            <div class="row">
              <input :id="`c-${c.token}`" v-model="form.colors[c.token]" type="color" />
              <code>{{ form.colors[c.token] }}</code>
            </div>
          </div>
        </div>
        <div class="row">
          <div class="grow">
            <label for="f-heading">Шрифт заголовков</label>
            <input id="f-heading" v-model="form.heading" type="text" list="fonts" maxlength="64" />
          </div>
          <div class="grow">
            <label for="f-body">Шрифт текста</label>
            <input id="f-body" v-model="form.body" type="text" list="fonts" maxlength="64" />
          </div>
          <datalist id="fonts">
            <option v-for="f in MEASURED_FONTS" :key="f" :value="f" />
          </datalist>
        </div>
        <p class="muted small">
          Точно сервер измеряет {{ MEASURED_FONTS.join(", ") }}. Другой шрифт тоже попадёт в
          презентацию, но строки в PowerPoint могут переноситься иначе.
        </p>
        <button type="submit" class="secondary" :disabled="busy">Сохранить оформление</button>
      </form>
    </details>

    <div class="logo">
      <h3>Логотип</h3>
      <div class="row">
        <img
          v-if="project.has_logo"
          :src="brand.logoUrl(project.id, logoVersion)"
          alt="Логотип проекта"
          class="logo-img"
        />
        <span v-else class="muted small">Логотипа нет</span>
        <label class="button secondary file-button">
          {{ project.has_logo ? "Заменить" : "Загрузить PNG" }}
          <input type="file" accept="image/png" class="visually-hidden" @change="uploadLogo" />
        </label>
        <button v-if="project.has_logo" type="button" class="icon" @click="removeLogo">
          Убрать
        </button>
      </div>
    </div>

    <div
      v-if="message"
      class="alert"
      :class="{
        'alert-ok': message.kind === 'ok',
        'alert-bad': message.kind === 'bad',
        'alert-warn': message.kind === 'warn',
      }"
      role="status"
    >
      {{ message.text }}
      <ul v-if="message.list">
        <li v-for="item in message.list" :key="item">{{ item }}</li>
      </ul>
    </div>
  </section>
</template>

<style scoped>
.swatches {
  display: flex;
  gap: 6px;
}
.swatch {
  width: 28px;
  height: 28px;
  border-radius: 6px;
  border: 1px solid var(--line);
}
.file-button {
  margin: 0;
  font-size: 15px;
}
.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  opacity: 0;
}
.file-button {
  position: relative;
}
.file-button:focus-within {
  outline: 2px solid var(--accent-2);
  outline-offset: 1px;
}
.colors {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 10px;
}
.color input[type="color"] {
  width: 44px;
  height: 32px;
  padding: 0;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: none;
}
.grow {
  flex: 1;
  min-width: 140px;
}
.form {
  margin-top: 12px;
}
summary {
  cursor: pointer;
  font-weight: 600;
  color: var(--accent);
}
.logo h3 {
  margin-bottom: 8px;
}
.logo-img {
  max-height: 40px;
  max-width: 160px;
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 4px;
  background: #fff;
}
</style>
