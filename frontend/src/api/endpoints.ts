/** One function per API call the UI makes. */

import { apiDelete, apiGet, apiPatch, apiPost, apiPut } from "./client";
import type {
  Artifact,
  BrandOut,
  FileOut,
  Job,
  Json,
  Project,
  ProjectKind,
  Scenario,
  ScenarioSummary,
  User,
} from "./types";

const enc = encodeURIComponent;

export const auth = {
  me: () => apiGet<User>("/auth/me"),
  login: (email: string, password: string) => apiPost<User>("/auth/login", { email, password }),
  register: (email: string, password: string) =>
    apiPost<User>("/auth/register", { email, password }),
  logout: () => apiPost<void>("/auth/logout"),
};

export const projects = {
  list: () => apiGet<Project[]>("/projects"),
  get: (id: string) => apiGet<Project>(`/projects/${enc(id)}`),
  create: (title: string, kind: ProjectKind) => apiPost<Project>("/projects", { title, kind }),
  update: (id: string, patch: { title?: string; settings?: Json }) =>
    apiPatch<Project>(`/projects/${enc(id)}`, patch),
  remove: (id: string) => apiDelete(`/projects/${enc(id)}`),
  uploadFile: (id: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return apiPost<FileOut>(`/projects/${enc(id)}/files`, form);
  },
  addText: (id: string, title: string, text: string) =>
    apiPost<FileOut>(`/projects/${enc(id)}/text`, { title, text }),
  removeFile: (id: string, fileId: string) =>
    apiDelete(`/projects/${enc(id)}/files/${enc(fileId)}`),
};

export const brand = {
  set: (id: string, value: Json) => apiPut<BrandOut>(`/projects/${enc(id)}/brand`, value),
  reset: (id: string) => apiDelete(`/projects/${enc(id)}/brand`),
  setLogo: (id: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return apiPut<void>(`/projects/${enc(id)}/logo`, form);
  },
  removeLogo: (id: string) => apiDelete(`/projects/${enc(id)}/logo`),
  logoUrl: (id: string, version: string) => `/api/projects/${enc(id)}/logo?v=${enc(version)}`,
};

export const jobs = {
  plan: (projectId: string) => apiPost<Job>(`/projects/${enc(projectId)}/plan`),
  render: (projectId: string, version?: number) =>
    apiPost<Job>(`/projects/${enc(projectId)}/render`, version ? { version } : {}),
  get: (id: string) => apiGet<Job>(`/jobs/${enc(id)}`),
  ofProject: (projectId: string) => apiGet<Job[]>(`/projects/${enc(projectId)}/jobs`),
};

export const scenarios = {
  latest: (projectId: string, version?: number) =>
    apiGet<Scenario>(`/projects/${enc(projectId)}/scenario${version ? `?version=${version}` : ""}`),
  versions: (projectId: string) =>
    apiGet<ScenarioSummary[]>(`/projects/${enc(projectId)}/scenarios`),
  save: (projectId: string, baseVersion: number, content: Json) =>
    apiPut<Scenario>(`/projects/${enc(projectId)}/scenario`, {
      base_version: baseVersion,
      content,
    }),
};

export const artifacts = {
  ofProject: (projectId: string) => apiGet<Artifact[]>(`/projects/${enc(projectId)}/artifacts`),
};
