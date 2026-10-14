/**
 * Everything one project page works with: the project, its latest scenario, the plan and
 * render jobs being followed, history and output files. Created by ProjectView and handed to
 * the steps with provide/inject.
 */

import {
  computed,
  inject,
  provide,
  ref,
  watch,
  type ComputedRef,
  type InjectionKey,
  type Ref,
} from "vue";

import { ApiError, errorText } from "../api/client";
import { artifacts, jobs, projects, scenarios } from "../api/endpoints";
import type { Artifact, Job, Json, Project, Scenario } from "../api/types";
import { clone, same } from "../lib/scenario";
import { useJob } from "./useJob";

export interface Workspace {
  project: Ref<Project | null>;
  scenario: Ref<Scenario | null>;
  history: Ref<Job[]>;
  outputs: Ref<Artifact[]>;
  planJob: Ref<Job | null>;
  renderJob: Ref<Job | null>;
  error: Ref<string | null>;
  /** The scenario being edited. Lives here, not in the step, so switching steps keeps it. */
  draft: Ref<Json | null>;
  /** JSON-tab text that does not parse yet: saving would silently drop it. */
  jsonInvalid: Ref<boolean>;
  dirty: ComputedRef<boolean>;
  reloadProject(): Promise<void>;
  reloadScenario(version?: number): Promise<void>;
  startPlan(): Promise<void>;
  startRender(version?: number): Promise<void>;
  load(): Promise<void>;
}

const KEY: InjectionKey<Workspace> = Symbol("workspace");

export function provideWorkspace(id: string): Workspace {
  const project = ref<Project | null>(null);
  const scenario = ref<Scenario | null>(null);
  const history = ref<Job[]>([]);
  const outputs = ref<Artifact[]>([]);
  const error = ref<string | null>(null);

  async function reloadHistory(): Promise<void> {
    history.value = await jobs.ofProject(id);
  }

  async function reloadOutputs(): Promise<void> {
    outputs.value = await artifacts.ofProject(id);
  }

  async function reloadScenario(version?: number): Promise<void> {
    try {
      scenario.value = await scenarios.latest(id, version);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) scenario.value = null;
      else throw err;
    }
  }

  const plan = useJob(() => {
    void Promise.all([reloadScenario(), reloadHistory()]).catch((err) => {
      error.value = errorText(err);
    });
  });
  const render = useJob(() => {
    void Promise.all([reloadOutputs(), reloadHistory()]).catch((err) => {
      error.value = errorText(err);
    });
  });

  // The history table shows the jobs being followed as they progress.
  for (const followed of [plan.job, render.job]) {
    watch(followed, (job) => {
      if (job) history.value = history.value.map((j) => (j.id === job.id ? job : j));
    });
  }

  const draft = ref<Json | null>(null);
  const jsonInvalid = ref(false);
  watch(scenario, (s) => {
    draft.value = s ? clone(s.content) : null;
    jsonInvalid.value = false;
  });
  const dirty = computed(
    () =>
      jsonInvalid.value ||
      (!!draft.value && !!scenario.value && !same(draft.value, scenario.value.content)),
  );

  async function reloadProject(): Promise<void> {
    project.value = await projects.get(id);
  }

  async function load(): Promise<void> {
    error.value = null;
    try {
      await Promise.all([reloadProject(), reloadScenario(), reloadHistory(), reloadOutputs()]);
      const lastPlan = history.value.find((j) => j.type === "plan");
      const lastRender = history.value.find((j) => j.type === "render");
      if (lastPlan) plan.follow(lastPlan);
      if (lastRender) render.follow(lastRender);
    } catch (err) {
      error.value = errorText(err);
    }
  }

  async function startPlan(): Promise<void> {
    const job = await jobs.plan(id);
    history.value = [job, ...history.value];
    plan.follow(job);
  }

  async function startRender(version?: number): Promise<void> {
    const job = await jobs.render(id, version);
    history.value = [job, ...history.value];
    render.follow(job);
  }

  const workspace: Workspace = {
    project,
    scenario,
    history,
    outputs,
    planJob: plan.job,
    renderJob: render.job,
    error,
    draft,
    jsonInvalid,
    dirty,
    reloadProject,
    reloadScenario,
    startPlan,
    startRender,
    load,
  };
  provide(KEY, workspace);
  return workspace;
}

export function useWorkspace(): Workspace {
  const workspace = inject(KEY);
  if (!workspace) throw new Error("useWorkspace() outside a project page");
  return workspace;
}
