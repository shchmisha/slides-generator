import { createRouter, createWebHistory } from "vue-router";

import { setUnauthorizedHandler } from "./api/client";
import { loadUser, setUser } from "./composables/session";
import LoginView from "./views/LoginView.vue";
import ProjectsView from "./views/ProjectsView.vue";
import ProjectView from "./views/ProjectView.vue";

export const STEPS = ["materials", "scenario", "result", "history"] as const;
export type Step = (typeof STEPS)[number];

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/login", name: "login", component: LoginView, meta: { public: true } },
    { path: "/", redirect: "/projects" },
    { path: "/projects", name: "projects", component: ProjectsView },
    {
      path: "/projects/:id/:step(materials|scenario|result|history)?",
      name: "project",
      component: ProjectView,
      props: true,
    },
    { path: "/:rest(.*)*", redirect: "/projects" },
  ],
});

router.beforeEach(async (to) => {
  if (to.meta.public) return true;
  // Only "not signed in" (401) sends to the login page; if the API is briefly unreachable the
  // page opens and shows its own error instead.
  const user = await loadUser().catch(() => undefined);
  return user === null ? { name: "login", query: { next: to.fullPath } } : true;
});

setUnauthorizedHandler(() => {
  setUser(null);
  const here = router.currentRoute.value;
  if (!here.meta.public) void router.push({ name: "login", query: { next: here.fullPath } });
});
