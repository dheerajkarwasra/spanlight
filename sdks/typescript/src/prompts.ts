/**
 * Prompt fetching, caching (TTL + ETag revalidation + stale fallback) and template compilation.
 * Mirrors `sdks/python/src/spanlight/_prompt_client.py`, `_prompts.py` and `_templates.py`.
 */

import { AsyncLocalStorage } from "node:async_hooks";

import { log } from "./log.js";
import { VERSION } from "./version.js";

export const MAX_TEMPLATE_BYTES = 64 * 1024;
export const MAX_CHAT_MESSAGES = 50;
export const MAX_COMPILED_BYTES = 256 * 1024;

export type PromptRole = "system" | "user" | "assistant";
export type PromptKind = "text" | "chat";

export interface PromptMessage {
  role: PromptRole;
  content: string;
}

export type PromptFallback = string | PromptMessage[];

export interface GetPromptOptions {
  label?: string;
  version?: number;
  fallback?: PromptFallback;
}

export class PromptUnavailableError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PromptUnavailableError";
  }
}

export class InvalidTemplateError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "InvalidTemplateError";
  }
}

export class MissingVariablesError extends Error {
  readonly names: string[];
  constructor(names: string[]) {
    super(`missing variables: ${names.join(", ")}`);
    this.name = "MissingVariablesError";
    this.names = names;
  }
}

export class TemplateTooLargeError extends Error {
  constructor() {
    super(`compiled prompt exceeds ${MAX_COMPILED_BYTES} bytes`);
    this.name = "TemplateTooLargeError";
  }
}

const VALID_ROLES: ReadonlySet<string> = new Set(["system", "user", "assistant"]);
const VAR_NAME = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/;
const TOKEN = /\{\{([^\n]*?)\}\}|\{\{/g;
const encoder = new TextEncoder();

interface Part {
  literal: string;
  name?: string;
}

function scanTemplate(source: string): Part[] {
  const parts: Part[] = [];
  let cursor = 0;
  TOKEN.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = TOKEN.exec(source)) !== null) {
    if (match.index > cursor) parts.push({ literal: source.slice(cursor, match.index) });
    const inner = match[1];
    if (inner === undefined) throw new InvalidTemplateError("unclosed '{{' in template");
    const name = inner.trim();
    if (!VAR_NAME.test(name)) {
      throw new InvalidTemplateError(`invalid variable placeholder '{{${inner}}}'`);
    }
    parts.push({ literal: match[0], name });
    cursor = match.index + match[0].length;
  }
  if (cursor < source.length) parts.push({ literal: source.slice(cursor) });
  return parts;
}

function collectVariables(parts: Part[], seen: Set<string>, out: string[]): void {
  for (const part of parts) {
    if (part.name !== undefined && !seen.has(part.name)) {
      seen.add(part.name);
      out.push(part.name);
    }
  }
}

const promptVersionContext = new AsyncLocalStorage<string | null>();

/** Return the active prompt version id from `withPrompt` / `compiled.run(...)`, if any. */
export function getCurrentPromptVersionId(): string | undefined {
  return promptVersionContext.getStore() ?? undefined;
}

/** Run `fn` with no active prompt version link (clears inherited `withPrompt` context). */
export function noPrompt<T>(fn: () => T): T {
  return promptVersionContext.run(null, fn);
}

/** Compiled prompt ready to pass to an LLM and optionally bind to spans via `run(...)`. */
export class CompiledPrompt {
  constructor(
    readonly name: string,
    readonly version: number | undefined,
    readonly promptVersionId: string | undefined,
    readonly kind: PromptKind,
    readonly text: string | undefined,
    readonly messages: PromptMessage[] | undefined,
    readonly variablesUsed: string[],
    readonly unusedVariables: string[],
  ) {}

  /** Run `fn` linking every span started inside it to this prompt version (or unlinked if fallback). */
  run<T>(fn: () => T): T {
    return promptVersionContext.run(this.promptVersionId ?? null, fn);
  }
}

/** Run `fn` linking every span started inside it to `compiled`'s prompt version. */
export function withPrompt<T>(compiled: CompiledPrompt, fn: () => T): T {
  return compiled.run(fn);
}

/** Immutable prompt version (or local fallback). */
export class Prompt {
  readonly variables: string[];

  constructor(
    readonly name: string,
    readonly version: number | undefined,
    readonly versionId: string | undefined,
    readonly kind: PromptKind,
    readonly text: string | undefined,
    readonly messages: PromptMessage[] | undefined,
    readonly modelConfig: Readonly<Record<string, unknown>> = {},
    readonly labels: readonly string[] = [],
  ) {
    const seen = new Set<string>();
    const vars: string[] = [];
    if (kind === "text" && text !== undefined) collectVariables(scanTemplate(text), seen, vars);
    if (kind === "chat" && messages !== undefined) {
      for (const msg of messages) collectVariables(scanTemplate(msg.content), seen, vars);
    }
    this.variables = vars;
  }

  get isFallback(): boolean {
    return this.version === undefined;
  }

  /** Compile template with `variables` (`string | number | boolean`). */
  compile(variables: Record<string, string | number | boolean> = {}): CompiledPrompt {
    const strVars: Record<string, string> = {};
    for (const [k, v] of Object.entries(variables)) {
      if (typeof v === "string") strVars[k] = v;
      else if (typeof v === "number" || typeof v === "boolean") strVars[k] = String(v);
      else throw new TypeError(`variable '${k}' must be a string, number, or boolean`);
    }
    const missing = this.variables.filter((name) => !Object.hasOwn(strVars, name)).sort();
    if (missing.length > 0) throw new MissingVariablesError(missing);
    const usedSet = new Set(this.variables);
    const unused = Object.keys(strVars)
      .filter((k) => !usedSet.has(k))
      .sort();
    let totalBytes = 0;
    const render = (source: string): string => {
      const rendered = scanTemplate(source)
        .map((p) => (p.name !== undefined ? (strVars[p.name] ?? "") : p.literal))
        .join("");
      totalBytes += encoder.encode(rendered).byteLength;
      if (totalBytes > MAX_COMPILED_BYTES) throw new TemplateTooLargeError();
      return rendered;
    };
    if (this.kind === "text") {
      return new CompiledPrompt(
        this.name,
        this.version,
        this.versionId,
        "text",
        render(this.text ?? ""),
        undefined,
        [...this.variables],
        unused,
      );
    }
    const compiledMessages = (this.messages ?? []).map((m) => ({
      role: m.role,
      content: render(m.content),
    }));
    return new CompiledPrompt(
      this.name,
      this.version,
      this.versionId,
      "chat",
      undefined,
      compiledMessages,
      [...this.variables],
      unused,
    );
  }
}

function parseWireMessages(raw: unknown): PromptMessage[] {
  if (!Array.isArray(raw) || raw.length === 0 || raw.length > MAX_CHAT_MESSAGES) {
    throw new InvalidTemplateError("chat template must have 1 to 50 messages");
  }
  return raw.map((item) => {
    if (item === null || typeof item !== "object") {
      throw new InvalidTemplateError("message must be an object");
    }
    const role = (item as Record<string, unknown>).role;
    const content = (item as Record<string, unknown>).content;
    if (typeof role !== "string" || !VALID_ROLES.has(role) || typeof content !== "string") {
      throw new InvalidTemplateError("message requires valid role and string content");
    }
    if (encoder.encode(content).byteLength > MAX_TEMPLATE_BYTES) {
      throw new InvalidTemplateError("template exceeds 64 KB");
    }
    return { role: role as PromptRole, content };
  });
}

function promptFromWire(body: unknown): Prompt {
  if (body === null || typeof body !== "object")
    throw new Error("prompt response is not an object");
  const obj = body as Record<string, unknown>;
  const { name, version, version_id: versionId, template } = obj;
  if (typeof name !== "string" || typeof versionId !== "string" || typeof version !== "number") {
    throw new Error("prompt response lacks name, version, or version_id");
  }
  if (template === null || typeof template !== "object") throw new Error("invalid template");
  const tpl = template as Record<string, unknown>;
  let text: string | undefined;
  let messages: PromptMessage[] | undefined;
  let kind: PromptKind;
  if (typeof tpl.text === "string") {
    if (encoder.encode(tpl.text).byteLength > MAX_TEMPLATE_BYTES) {
      throw new InvalidTemplateError("template exceeds 64 KB");
    }
    kind = "text";
    text = tpl.text;
  } else {
    kind = "chat";
    messages = parseWireMessages(tpl.messages);
  }
  const modelConfig =
    obj.model_config !== null && typeof obj.model_config === "object"
      ? (obj.model_config as Record<string, unknown>)
      : {};
  const labels = Array.isArray(obj.labels) ? obj.labels.map(String) : [];
  return new Prompt(name, version, versionId, kind, text, messages, modelConfig, labels);
}

function fallbackPrompt(name: string, fallback: PromptFallback): Prompt {
  if (typeof fallback === "string") {
    if (encoder.encode(fallback).byteLength > MAX_TEMPLATE_BYTES) {
      throw new InvalidTemplateError("template exceeds 64 KB");
    }
    return new Prompt(name, undefined, undefined, "text", fallback, undefined);
  }
  return new Prompt(name, undefined, undefined, "chat", undefined, parseWireMessages(fallback));
}

interface CacheEntry {
  prompt: Prompt;
  etag: string | undefined;
  expiresAt: number;
}

const FAILURE_MEMO_MS = 5_000;

export class PromptClient {
  private readonly cache = new Map<string, CacheEntry>();
  private readonly failures = new Map<string, { until: number; message: string }>();
  private readonly inflight = new Map<string, Promise<Prompt>>();
  readonly #apiKey: string | undefined;

  constructor(
    private readonly baseUrl: string,
    apiKey: string | undefined,
    private readonly timeoutMs: number,
    private readonly cacheTtlMs: number,
    private readonly fetchFn: typeof fetch,
  ) {
    this.#apiKey = apiKey;
  }

  async get(name: string, options: GetPromptOptions = {}): Promise<Prompt> {
    const label = options.label ?? "production";
    const version = options.version;
    const key = `${name}\0${label}\0${version ?? ""}`;
    const now = Date.now();
    const cached = this.cache.get(key);
    if (cached !== undefined && cached.expiresAt > now) return cached.prompt;

    const existing = this.inflight.get(key);
    if (existing !== undefined) {
      try {
        return await existing;
      } catch (err) {
        if (options.fallback !== undefined) return fallbackPrompt(name, options.fallback);
        throw err;
      }
    }

    const promise = this.fetchPrompt(key, name, label, version, cached);
    this.inflight.set(key, promise);
    try {
      return await promise;
    } catch (err) {
      if (options.fallback !== undefined) return fallbackPrompt(name, options.fallback);
      throw err;
    } finally {
      this.inflight.delete(key);
    }
  }

  private async fetchPrompt(
    key: string,
    name: string,
    label: string,
    version: number | undefined,
    cached: CacheEntry | undefined,
  ): Promise<Prompt> {
    const now = Date.now();
    const failure = this.failures.get(key);
    if (failure !== undefined && failure.until > now) {
      if (cached !== undefined) return cached.prompt;
      throw new PromptUnavailableError(failure.message);
    }
    if (!this.#apiKey) {
      const msg = "Spanlight API key is not configured or host is unsafe";
      if (cached !== undefined) return cached.prompt;
      throw new PromptUnavailableError(msg);
    }

    const encodedName = name.split("/").map(encodeURIComponent).join("/");
    const query =
      version !== undefined
        ? `version=${encodeURIComponent(String(version))}`
        : `label=${encodeURIComponent(label)}`;
    const headers: Record<string, string> = {
      Authorization: `Bearer ${this.#apiKey}`,
      "User-Agent": `spanlight-js/${VERSION}`,
    };
    if (cached?.etag !== undefined) headers["If-None-Match"] = cached.etag;

    try {
      const response = await this.fetchFn(`${this.baseUrl}/${encodedName}?${query}`, {
        method: "GET",
        headers,
        signal: AbortSignal.timeout(this.timeoutMs),
      });
      if (response.status === 304 && cached !== undefined) {
        const refreshed: CacheEntry = {
          prompt: cached.prompt,
          etag: response.headers.get("etag") ?? cached.etag,
          expiresAt: Date.now() + this.cacheTtlMs,
        };
        this.cache.set(key, refreshed);
        this.failures.delete(key);
        return cached.prompt;
      }
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const prompt = promptFromWire(await response.json());
      this.cache.set(key, {
        prompt,
        etag: response.headers.get("etag") ?? undefined,
        expiresAt: Date.now() + this.cacheTtlMs,
      });
      this.failures.delete(key);
      return prompt;
    } catch (err) {
      const reason = `Could not fetch prompt '${name}': ${err instanceof Error ? err.message : String(err)}`;
      log.warn(reason);
      this.failures.set(key, { until: Date.now() + FAILURE_MEMO_MS, message: reason });
      if (cached !== undefined) return cached.prompt;
      throw new PromptUnavailableError(reason);
    }
  }
}
