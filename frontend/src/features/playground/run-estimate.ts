/**
 * What a playground run may cost at list prices, before it runs. Pure: no I/O.
 *
 * Input tokens are estimated from the compiled prompt (or the filled messages) and every allowed
 * output token counts, as the backend's call estimate does (`evals/budget.py`). Money is summed
 * as exact decimals: the rates arrive as decimal strings.
 */
import type { Credential, Price } from "@/lib/api";
import { compileTemplate } from "@/lib/prompt-template";

import {
  MAX_TOKENS_RULE,
  effectiveCredentialId,
  filledMessages,
  parseParam,
  type ColumnState,
} from "./config-state";

/** `playground/request.py` DEFAULT_ANTHROPIC_MAX_TOKENS: the server sends it when unset. */
const DEFAULT_ANTHROPIC_MAX_TOKENS = 1024;

/** `core/model_match.py`: a snapshot suffix after the configured name. */
const SNAPSHOT_SUFFIX = /^-([0-9]{8}|[0-9]{4}-[0-9]{2}-[0-9]{2}|latest)$/;

/** Rates and costs as integers of 10^-SCALE, so sums are exact. */
const SCALE = 18;

export type UnknownReason = "no_price" | "no_max_tokens";
export type ColumnEstimate = { usd: string } | { unknown: UnknownReason; model: string };
export type RunEstimate =
  { usd: string } | { unknown: { column: number; reason: UnknownReason; model: string }[] };

/** One column and the provider of its credential; null when no credential is known. */
export interface ColumnEstimateInput {
  column: ColumnState;
  provider: string | null;
}

/** Mirrors `backend/app/pricing/tokens.py`: ceil(ASCII characters / 3) + every other character. */
export function estimateTokens(text: string): number {
  let ascii = 0;
  let other = 0;
  // Iterating a string yields code points, as Python's `len` counts them.
  for (const char of text) {
    if ((char.codePointAt(0) ?? 0) < 128) ascii += 1;
    else other += 1;
  }
  return Math.ceil(ascii / 3) + other;
}

function matchesModel(pattern: string, model: string): boolean {
  return (
    model === pattern ||
    (model.startsWith(pattern) && SNAPSHOT_SUFFIX.test(model.slice(pattern.length)))
  );
}

/**
 * Mirrors `best_price` in `backend/app/pricing/price_row.py`: rows in force at `at` whose pattern
 * matches exactly or with a snapshot suffix (never by prefix), then the highest
 * `min_prompt_tokens` not above the prompt, the longest pattern, the latest `effective_from`,
 * the greatest `version`. A null provider matches every row.
 */
export function pickPrice(
  prices: readonly Price[],
  provider: string | null,
  model: string,
  inputTokens: number,
  at: Date,
): Price | null {
  let best: Price | null = null;
  for (const price of prices) {
    if (
      Date.parse(price.effective_from) > at.getTime() ||
      price.min_prompt_tokens > inputTokens ||
      (provider !== null && price.provider !== provider) ||
      !matchesModel(price.model_pattern, model)
    ) {
      continue;
    }
    if (best === null || outranks(price, best)) best = price;
  }
  return best;
}

function outranks(a: Price, b: Price): boolean {
  if (a.min_prompt_tokens !== b.min_prompt_tokens) return a.min_prompt_tokens > b.min_prompt_tokens;
  if (a.model_pattern.length !== b.model_pattern.length) {
    return a.model_pattern.length > b.model_pattern.length;
  }
  const from = Date.parse(a.effective_from) - Date.parse(b.effective_from);
  return from !== 0 ? from > 0 : a.version > b.version;
}

/** A non-negative decimal string as an integer of 10^-SCALE; null when it is not one. */
function toScaled(value: string): bigint | null {
  const match = /^(\d+)(?:\.(\d+))?$/.exec(value.trim());
  if (!match) return null;
  const fraction = (match[2] ?? "").slice(0, SCALE).padEnd(SCALE, "0");
  return BigInt(match[1] ?? "0") * 10n ** BigInt(SCALE) + BigInt(fraction);
}

function fromScaled(value: bigint): string {
  const unit = 10n ** BigInt(SCALE);
  const fraction = (value % unit).toString().padStart(SCALE, "0").replace(/0+$/, "");
  return `${value / unit}${fraction ? `.${fraction}` : ""}`;
}

/** The text the provider gets: the compiled prompt, or the filled messages, concatenated. */
function inputText(column: ColumnState): string {
  if (column.sourceKind === "messages") {
    return filledMessages(column)
      .map((message) => message.content)
      .join("");
  }
  if (!column.version) return "";
  try {
    const compiled = compileTemplate(column.version.template, column.variables);
    return compiled.text ?? (compiled.messages ?? []).map((message) => message.content).join("");
  } catch {
    // A prompt that does not compile cannot run; the column shows its own problem.
    return "";
  }
}

function columnCost(
  column: ColumnState,
  provider: string | null,
  prices: readonly Price[],
  at: Date,
): bigint | { unknown: UnknownReason; model: string } {
  const model = column.model.trim();
  const inputTokens = estimateTokens(inputText(column));
  const price = provider === null ? null : pickPrice(prices, provider, model, inputTokens, at);
  const inputRate = price ? toScaled(price.input_per_mtok) : null;
  const outputRate = price ? toScaled(price.output_per_mtok) : null;
  if (inputRate === null || outputRate === null) return { unknown: "no_price", model };
  const maxTokens = parseParam(column.maxTokens, MAX_TOKENS_RULE);
  const outputTokens =
    typeof maxTokens === "number"
      ? maxTokens
      : provider === "anthropic"
        ? DEFAULT_ANTHROPIC_MAX_TOKENS
        : null;
  if (outputTokens === null) return { unknown: "no_max_tokens", model };
  return (BigInt(inputTokens) * inputRate + BigInt(outputTokens) * outputRate) / 1_000_000n;
}

/** The most one column's call can cost at list prices, in USD, or why that is unknown. */
export function estimateColumn(
  column: ColumnState,
  provider: string | null,
  prices: readonly Price[],
  at: Date,
): ColumnEstimate {
  const cost = columnCost(column, provider, prices, at);
  return typeof cost === "bigint" ? { usd: fromScaled(cost) } : cost;
}

/** The whole run's ceiling, or every column (by index) whose cost is unknown. */
export function estimateRun(
  columns: readonly ColumnEstimateInput[],
  prices: readonly Price[],
  at: Date,
): RunEstimate {
  let total = 0n;
  const unknown: { column: number; reason: UnknownReason; model: string }[] = [];
  for (const [index, { column, provider }] of columns.entries()) {
    const cost = columnCost(column, provider, prices, at);
    if (typeof cost === "bigint") total += cost;
    else unknown.push({ column: index, reason: cost.unknown, model: cost.model });
  }
  return unknown.length > 0 ? { unknown } : { usd: fromScaled(total) };
}

/**
 * The page's estimate, or null while the credentials or prices load, or while a column has no
 * model or no resolved credential: a missing input is not a missing price.
 */
export function estimateForColumns(
  columns: readonly ColumnState[],
  credentials: readonly Credential[] | undefined,
  prices: readonly Price[] | undefined,
  at: Date,
): RunEstimate | null {
  if (!credentials || !prices) return null;
  const ids = credentials.map((credential) => credential.id);
  const inputs: ColumnEstimateInput[] = [];
  for (const column of columns) {
    const id = effectiveCredentialId(column, ids);
    const credential = credentials.find((candidate) => candidate.id === id);
    if (!credential || column.model.trim() === "") return null;
    inputs.push({ column, provider: credential.provider });
  }
  return estimateRun(inputs, prices, at);
}

/** "$0.004": up to 6 decimals (at least 2), rounded up; below a millionth, "< $0.000001". */
export function formatEstimateUsd(usd: string): string {
  const scaled = toScaled(usd) ?? 0n;
  const micro = 10n ** BigInt(SCALE - 6);
  if (scaled > 0n && scaled < micro) return "< $0.000001";
  // Rounded up: the line is a ceiling, so it never reads below the real one.
  const micros = (scaled + micro - 1n) / micro;
  const fraction = (micros % 1_000_000n)
    .toString()
    .padStart(6, "0")
    .replace(/0{1,4}$/, "");
  return `$${micros / 1_000_000n}.${fraction}`;
}

/** The run button: one call says "Run"; more say how many paid calls go out. */
export function runLabel(columns: number): string {
  return columns > 1 ? `Run ${columns} calls` : "Run";
}
