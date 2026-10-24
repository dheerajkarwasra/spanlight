import { z } from "zod";

import type { PriceOverrideCreate } from "@/lib/api";
import { projectSearchSchema } from "@/lib/time-range";

/**
 * Spec ruling 8: `/pricing?provider=openai&model=ft:custom-v2` opens the override dialog
 * prefilled. An unreadable param is dropped, never an error page; the router may read a numeric
 * model name as a number, so both are coerced back to text.
 */
export const pricingSearchSchema = projectSearchSchema.extend({
  provider: z.coerce.string().max(64).optional().catch(undefined),
  model: z.coerce.string().max(256).optional().catch(undefined),
});

/*
 * The override dialog's form. Bounds mirror `api/schemas/prices.py`: provider 1–100 and pattern
 * 1–200 characters after trimming; rates `numeric(12, 6)`, 0 or more and below 1 000 000.
 * Field names are the API's, so 422 field errors land on their fields as they are.
 */

export const PROVIDER_MAX_LENGTH = 100;
export const PATTERN_MAX_LENGTH = 200;
const RATE = /^(\d{1,6}(\.\d{0,6})?|\.\d{1,6})$/;
const RATE_MESSAGE = "Enter 0 or more, below 1,000,000, with at most 6 decimals.";

function text(label: string, max: number) {
  return z
    .string()
    .refine((value) => value.trim() !== "", `Enter a ${label}.`)
    .refine((value) => value.trim().length <= max, `Use at most ${max} characters.`);
}

const requiredRate = z
  .string()
  .refine((value) => value !== "", "Enter a rate per 1M tokens.")
  .refine((value) => value === "" || RATE.test(value), RATE_MESSAGE);
const optionalRate = z.string().refine((value) => value === "" || RATE.test(value), RATE_MESSAGE);

function isDay(value: string): boolean {
  const at = new Date(`${value}T00:00:00Z`);
  return /^\d{4}-\d{2}-\d{2}$/.test(value) && !Number.isNaN(at.getTime())
    ? at.toISOString().slice(0, 10) === value
    : false;
}

export const overrideFormSchema = z.object({
  provider: text("provider", PROVIDER_MAX_LENGTH),
  model_pattern: text("model pattern", PATTERN_MAX_LENGTH),
  input_per_mtok: requiredRate,
  output_per_mtok: requiredRate,
  cached_input_per_mtok: optionalRate,
  cache_write_5m_per_mtok: optionalRate,
  cache_write_1h_per_mtok: optionalRate,
  effective_from: z.string().refine(isDay, "Pick the day the price starts."),
});

export type OverrideFormValues = z.infer<typeof overrideFormSchema>;

/**
 * The server stores the pattern lower-cased, but prices a span's model exactly as reported, case
 * included (`pricing/cost.py` `PriceBook.find`), so a pattern with capitals never prices calls
 * spelled that way. Null when the pattern is already lower-case.
 */
export function caseHint(pattern: string): string | null {
  const typed = pattern.trim();
  const saved = typed.toLowerCase();
  return saved === typed
    ? null
    : `Saved as ${saved}. Model names match exactly, case included, so calls reporting ${typed} won't use this price.`;
}

/** What a link or an "Add price" row fills in. */
export interface OverridePrefill {
  provider?: string | undefined;
  model_pattern?: string | undefined;
}

/** A blank form starting `today` (a UTC day), with the prefill's provider and pattern. */
export function emptyOverrideValues(
  initial: OverridePrefill | undefined,
  today: string,
): OverrideFormValues {
  return {
    provider: initial?.provider ?? "",
    model_pattern: initial?.model_pattern ?? "",
    input_per_mtok: "",
    output_per_mtok: "",
    cached_input_per_mtok: "",
    cache_write_5m_per_mtok: "",
    cache_write_1h_per_mtok: "",
    effective_from: today,
  };
}

/** "1." → "1", ".2" → "0.2": the decimal string the server parses. */
function rate(value: string): string {
  return value.replace(/\.$/, "").replace(/^\./, "0.");
}

function optional(value: string): string | null {
  return value === "" ? null : rate(value);
}

/** The create body: trimmed text, null for blank optional rates, the day's start in UTC. */
export function toOverrideInput(values: OverrideFormValues): PriceOverrideCreate {
  return {
    provider: values.provider.trim(),
    model_pattern: values.model_pattern.trim(),
    input_per_mtok: rate(values.input_per_mtok),
    output_per_mtok: rate(values.output_per_mtok),
    cached_input_per_mtok: optional(values.cached_input_per_mtok),
    cache_write_5m_per_mtok: optional(values.cache_write_5m_per_mtok),
    cache_write_1h_per_mtok: optional(values.cache_write_1h_per_mtok),
    effective_from: `${values.effective_from}T00:00:00Z`,
  };
}
