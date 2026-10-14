<script setup lang="ts">
import { computed, ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import { errorText } from "../api/client";
import { auth } from "../api/endpoints";
import { setUser } from "../composables/session";
import { safeNext } from "../lib/navigation";

const route = useRoute();
const router = useRouter();

const mode = ref<"login" | "register">("login");
const email = ref("");
const password = ref("");
const busy = ref(false);
const error = ref<string | null>(null);

const MIN_PASSWORD = 10;
const tooShort = computed(
  () =>
    mode.value === "register" && password.value.length > 0 && password.value.length < MIN_PASSWORD,
);

async function submit(): Promise<void> {
  if (tooShort.value) return;
  busy.value = true;
  error.value = null;
  try {
    const call = mode.value === "login" ? auth.login : auth.register;
    setUser(await call(email.value, password.value));
    await router.push(safeNext(route.query.next));
  } catch (err) {
    error.value = errorText(err);
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <section class="card narrow">
    <div class="tabs" role="tablist">
      <a
        href="#"
        role="tab"
        :aria-current="mode === 'login' ? 'page' : undefined"
        :aria-selected="mode === 'login'"
        @click.prevent="mode = 'login'"
        >Вход</a
      >
      <a
        href="#"
        role="tab"
        :aria-current="mode === 'register' ? 'page' : undefined"
        :aria-selected="mode === 'register'"
        @click.prevent="mode = 'register'"
        >Регистрация</a
      >
    </div>
    <form class="stack" @submit.prevent="submit">
      <div>
        <label for="email">Email</label>
        <input id="email" v-model.trim="email" type="email" autocomplete="email" required />
      </div>
      <div>
        <label for="password">Пароль</label>
        <input
          id="password"
          v-model="password"
          type="password"
          :autocomplete="mode === 'login' ? 'current-password' : 'new-password'"
          :minlength="mode === 'register' ? MIN_PASSWORD : 1"
          required
        />
        <p v-if="mode === 'register'" class="small" :class="tooShort ? 'alert-bad' : 'muted'">
          Не короче {{ MIN_PASSWORD }} символов
        </p>
      </div>
      <p v-if="error" class="alert alert-bad" role="alert">{{ error }}</p>
      <button type="submit" :disabled="busy || tooShort">
        {{ mode === "login" ? "Войти" : "Создать аккаунт" }}
      </button>
    </form>
  </section>
</template>
