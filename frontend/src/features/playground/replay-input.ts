import type { ReplayInput, ReplayProviderKind } from "@/lib/api";

import { MAX_TOKENS_RULE, parseParam } from "./config-state";
import { parseRequestBody } from "./replay-state";

/*
 * Pure: what the replay column starts from and what "Run replay" sends. Only what the user
 * changed is sent, so an untouched replay stays exact.
 */

type Body = Record<string, unknown> | null;

/** The request the replay started from and the overrides asked for, to word what changed. */
export interface ReplayRequestPair {
  before: Body;
  after: Body;
}

/**
 * The credential family a span's provider needs when the server could not say (a refused source
 * carries none): Anthropic speaks its own API; every other provider string speaks OpenAI's.
 */
export function providerKindOf(provider: string | null | undefined): ReplayProviderKind {
  return provider === "anthropic" ? "anthropic" : "openai";
}

/** The request as editable text: the stored body, else the span's raw input (a cut string). */
export function requestText(body: Body, spanInput: unknown): string {
  if (body !== null) {
    return JSON.stringify(body, null, 2);
  }
  if (typeof spanInput === "string") {
    return spanInput;
  }
  return spanInput === undefined || spanInput === null ? "" : JSON.stringify(spanInput, null, 2);
}

export interface ReplayRequestArgs {
  traceId: string;
  spanId: string;
  credentialId: string;
  /** The stored request; null when the source was refused and only an edit can be sent. */
  stored: Body;
  /** The request text, as stored or as edited. */
  text: string;
  model: string;
  /** The model the column opened with, to tell an override from an untouched field. */
  initialModel: string;
  maxTokens: string;
}

/** The body that will be sent, before the server's caps: the edit when there is one. */
export function sentBody(args: Pick<ReplayRequestArgs, "stored" | "text">): Body {
  return parseRequestBody(args.text) ?? args.stored;
}

/** `POST /playground/replays`. Call only when `canRun` holds (the text parses, no markers). */
export function buildReplayInput(args: ReplayRequestArgs): ReplayInput {
  const body = sentBody(args);
  const edited = args.stored === null || JSON.stringify(body) !== JSON.stringify(args.stored);
  const bodyModel = typeof body?.model === "string" ? body.model : null;
  const model = args.model.trim();
  // A model typed over the stored one is an override; an untouched field never undoes an edit.
  const override = bodyModel === null || (model !== args.initialModel && model !== bodyModel);
  const maxTokens = parseParam(args.maxTokens, MAX_TOKENS_RULE);
  return {
    trace_id: args.traceId,
    span_id: args.spanId,
    credential_id: args.credentialId,
    model: override ? model : null,
    max_tokens: typeof maxTokens === "number" ? maxTokens : null,
    edited_body: edited ? body : null,
  };
}

/** The request a replay started from and the caps and model asked for, to word `changed`. */
export function requestPair(input: ReplayInput, stored: Body): ReplayRequestPair {
  const before = input.edited_body ?? stored;
  const cap = input.max_tokens ?? undefined;
  return {
    before,
    after: {
      model: input.model ?? before?.model,
      max_tokens: cap,
      max_completion_tokens: cap,
      max_output_tokens: cap,
    },
  };
}
