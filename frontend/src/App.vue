<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";

import { errorText } from "./api/client";
import { auth } from "./api/endpoints";
import ServiceBanner from "./components/ServiceBanner.vue";
import { currentUser, setUser } from "./composables/session";

const router = useRouter();
const signOutError = ref<string | null>(null);

async function signOut(): Promise<void> {
  signOutError.value = null;
  // Leave the page first: a project with unsaved edits may ask to stay.
  if (await router.push({ name: "login" })) return;
  try {
    await auth.logout();
    setUser(null);
  } catch (err) {
    signOutError.value = `Не удалось выйти: ${errorText(err)}. Попробуйте ещё раз`;
  }
}
</script>

<template>
  <header class="topbar">
    <RouterLink to="/projects" class="topbar-brand">Генератор презентаций и роликов</RouterLink>
    <div v-if="currentUser" class="topbar-user">
      <span class="muted-light">{{ currentUser.email }}</span>
      <button type="button" class="link-light" @click="signOut">Выйти</button>
    </div>
  </header>
  <p v-if="signOutError" class="alert alert-bad banner-line" role="alert">{{ signOutError }}</p>
  <ServiceBanner v-if="currentUser" />
  <main class="page">
    <RouterView v-slot="{ Component, route }">
      <component :is="Component" :key="(route.params.id as string | undefined) ?? route.name" />
    </RouterView>
  </main>
</template>

<style scoped>
.banner-line {
  border-radius: 0;
  text-align: center;
}
</style>
