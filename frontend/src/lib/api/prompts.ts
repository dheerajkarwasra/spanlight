import { api } from "./client";
import { projectPath } from "./paths";
import type { Page } from "./types";

export type PromptKind = "text" | "chat";
export type ChatRole = "system" | "user" | "assistant";

export interface ChatMessage {
  role: ChatRole;
  content: string;
}

/** Text or chat; `{{name}}` marks a variable in the text or in the content of each message. */
export type PromptTemplate =
  { kind: "text"; text: string } | { kind: "chat"; messages: ChatMessage[] };

/** Free-form advice for clients; the server never applies it. */
export interface ModelAdvice {
  model?: string | null;
  temperature?: number | null;
  max_tokens?: number | null;
  top_p?: number | null;
}

export interface PromptLabel {
  label: string;
  version: number;
}

export interface PromptListItem {
  id: string;
  name: string;
  description: string | null;
  kind: PromptKind;
  latest_version: number;
  labels: PromptLabel[];
  updated_at: string;
}

/** One immutable version. `variables` is filled by the server from the template. */
export interface PromptVersion {
  id: string;
  version: number;
  kind: PromptKind;
  template: PromptTemplate;
  variables: string[];
  model_config: ModelAdvice;
  commit_message: string | null;
  created_by: string | null;
  created_at: string;
}

export interface Prompt {
  id: string;
  name: string;
  description: string | null;
  kind: PromptKind;
  created_at: string;
  updated_at: string;
  latest: PromptVersion;
  labels: PromptLabel[];
}

/** One version read by its id (as an evaluator or experiment names it), with its prompt's name. */
export interface PromptVersionById {
  prompt_name: string;
  version: number;
  kind: PromptKind;
  template: PromptTemplate;
  variables: string[];
  model_config: ModelAdvice;
  created_at: string;
}

/** The last seven days of traffic on one version; `cost_usd` and `p95_ms` are null when unknown. */
export interface PromptVersionUsage {
  traces: number;
  llm_calls: number;
  cost_usd: string | null;
  p95_ms: number | null;
  has_unpriced: boolean;
}

export interface PromptListQuery {
  q?: string | undefined;
  limit?: number | undefined;
  cursor?: string | null | undefined;
}

export interface VersionListQuery {
  limit?: number | undefined;
  cursor?: string | null | undefined;
}

export interface PromptCreate {
  /** Letters, digits, `_ . -`; `/` separates folders. At most 128 characters. */
  name: string;
  kind: PromptKind;
  description?: string | null;
  template: PromptTemplate;
  model_config?: ModelAdvice;
  commit_message?: string | null;
}

export interface VersionCreate {
  template: PromptTemplate;
  model_config?: ModelAdvice;
  commit_message?: string | null;
}

/*
 * A prompt name contains "/", so it travels as path segments. Like end-user ids, a name that is
 * "." or ".." or starts with "~" gets one leading "~", and the API strips exactly one.
 */

/** The name as the API expects it in a path, before percent-encoding the segments. */
export function toPathPromptName(name: string): string {
  return name === "." || name === ".." || name.startsWith("~") ? `~${name}` : name;
}

/** The real name from a path built by `toPathPromptName`. */
export function fromPathPromptName(pathName: string): string {
  return pathName.startsWith("~") ? pathName.slice(1) : pathName;
}

function promptsPath(projectId: string): string {
  return `${projectPath(projectId)}/prompts`;
}

function promptPath(projectId: string, name: string): string {
  const segments = toPathPromptName(name).split("/").map(encodeURIComponent);
  return `${promptsPath(projectId)}/${segments.join("/")}`;
}

/**
 * Prompts under `/projects/{id}/prompts`. Reads need `project:read`; writes need `prompts:write`
 * (member). Errors: `409 NAME_TAKEN`, `409 KIND_MISMATCH`, `409 NO_CHANGES`, `409 PROMPT_IN_USE`,
 * `422 LABEL_RESERVED`, `422 VALIDATION_ERROR` (bad template), `429 RATE_LIMITED`.
 */
export const promptsApi = {
  /** Most recently updated first. Keyset-paginated. */
  list: (projectId: string, query: PromptListQuery = {}): Promise<Page<PromptListItem>> =>
    api.get<Page<PromptListItem>>(promptsPath(projectId), { ...query }),
  /** Creates the prompt and its version 1 (201). */
  create: (projectId: string, input: PromptCreate): Promise<Prompt> =>
    api.post<Prompt>(promptsPath(projectId), input),
  get: (projectId: string, name: string): Promise<Prompt> =>
    api.get<Prompt>(promptPath(projectId, name)),
  /** `409 PROMPT_IN_USE` while an evaluator or experiment points at one of its versions. */
  delete: (projectId: string, name: string): Promise<void> =>
    api.delete(promptPath(projectId, name)),

  /** Newest first. Keyset-paginated. */
  versions: (
    projectId: string,
    name: string,
    query: VersionListQuery = {},
  ): Promise<Page<PromptVersion>> =>
    api.get<Page<PromptVersion>>(`${promptPath(projectId, name)}/versions`, { ...query }),
  version: (projectId: string, name: string, version: number): Promise<PromptVersion> =>
    api.get<PromptVersion>(`${promptPath(projectId, name)}/versions/${version}`),
  /** A new version (201). `409 NO_CHANGES` when it equals the latest one. */
  createVersion: (projectId: string, name: string, input: VersionCreate): Promise<PromptVersion> =>
    api.post<PromptVersion>(`${promptPath(projectId, name)}/versions`, input),
  versionUsage: (projectId: string, name: string, version: number): Promise<PromptVersionUsage> =>
    api.get<PromptVersionUsage>(`${promptPath(projectId, name)}/versions/${version}/usage`),

  /** Points the label at a version. `latest` is reserved (`422 LABEL_RESERVED`). */
  setLabel: (
    projectId: string,
    name: string,
    label: string,
    version: number,
  ): Promise<PromptLabel> =>
    api.put<PromptLabel>(`${promptPath(projectId, name)}/labels/${encodeURIComponent(label)}`, {
      version,
    }),
  deleteLabel: (projectId: string, name: string, label: string): Promise<void> =>
    api.delete(`${promptPath(projectId, name)}/labels/${encodeURIComponent(label)}`),

  /** A version by its id (`project:read`); `404` for another project's version. */
  versionById: (projectId: string, versionId: string): Promise<PromptVersionById> =>
    api.get<PromptVersionById>(
      `${projectPath(projectId)}/prompt-versions/${encodeURIComponent(versionId)}`,
    ),
};
