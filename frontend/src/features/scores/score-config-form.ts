/**
 * The score config dialog's fields, checked with the API's rules before anything is sent, and
 * turned into the create or edit body. Pure functions only.
 */
import type { ScoreConfig, ScoreConfigCreate, ScoreConfigUpdate, ScoreDataType } from "@/lib/api";
import { CATEGORY_LIMIT, VALUE_LIMIT, formatScoreNumber } from "@/lib/score-format";

export const NAME_PATTERN = /^[a-z0-9][a-z0-9_.-]{0,63}$/;
const DESCRIPTION_LIMIT = 500;
const MAX_CATEGORIES = 100;

export interface ConfigFormValues {
  name: string;
  dataType: ScoreDataType;
  min: string;
  max: string;
  /** Comma separated. */
  categories: string;
  description: string;
}

export type ConfigFormErrors = Partial<
  Record<"name" | "min" | "max" | "categories" | "description", string>
>;

export function emptyConfigForm(): ConfigFormValues {
  return { name: "", dataType: "numeric", min: "", max: "", categories: "", description: "" };
}

export function configFormOf(config: ScoreConfig): ConfigFormValues {
  return {
    name: config.name,
    dataType: config.data_type,
    min: config.min_value === null ? "" : formatScoreNumber(config.min_value),
    max: config.max_value === null ? "" : formatScoreNumber(config.max_value),
    categories: config.categories.join(", "),
    description: config.description ?? "",
  };
}

/** "good, ok, bad" → ["good", "ok", "bad"]: trimmed, blanks and repeats dropped. */
export function parseCategories(text: string): string[] {
  const seen = new Set<string>();
  for (const part of text.split(",")) {
    const category = part.trim();
    if (category !== "") {
      seen.add(category);
    }
  }
  return Array.from(seen);
}

/** A blank bound is an open end (null); anything else is a number within ±1e9, four decimals. */
function parseBound(text: string): number | null | "invalid" {
  const trimmed = text.trim();
  if (trimmed === "") {
    return null;
  }
  const value = Number(trimmed);
  const tooPrecise = /\.\d{5,}$/.test(trimmed);
  return Number.isFinite(value) && Math.abs(value) <= VALUE_LIMIT && !tooPrecise
    ? value
    : "invalid";
}

/** What is wrong with the fields, by field; an empty object when they can be sent. */
export function configFormErrors(values: ConfigFormValues, isEdit: boolean): ConfigFormErrors {
  const errors: ConfigFormErrors = {};
  if (!isEdit && !NAME_PATTERN.test(values.name.trim())) {
    errors.name =
      values.name.trim() === ""
        ? "Enter a name."
        : "Use lowercase letters, digits, _ . or -, starting with a letter or digit (up to 64 characters).";
  }
  if (values.dataType === "numeric") {
    const min = parseBound(values.min);
    const max = parseBound(values.max);
    if (min === "invalid") {
      errors.min = "Enter a number with up to four decimals, or leave it empty for no minimum.";
    }
    if (max === "invalid") {
      errors.max = "Enter a number with up to four decimals, or leave it empty for no maximum.";
    }
    if (typeof min === "number" && typeof max === "number" && min > max) {
      errors.max = "The maximum must be at least the minimum.";
    }
  }
  if (values.dataType === "categorical") {
    const categories = parseCategories(values.categories);
    if (categories.some((category) => category.length > CATEGORY_LIMIT)) {
      errors.categories = `Each value can have at most ${String(CATEGORY_LIMIT)} characters.`;
    } else if (categories.length > MAX_CATEGORIES) {
      errors.categories = `List at most ${String(MAX_CATEGORIES)} values.`;
    }
  }
  if (values.description.trim().length > DESCRIPTION_LIMIT) {
    errors.description = `Use at most ${String(DESCRIPTION_LIMIT)} characters.`;
  }
  return errors;
}

function typedFields(values: ConfigFormValues) {
  const min = parseBound(values.min);
  const max = parseBound(values.max);
  const numeric = values.dataType === "numeric";
  return {
    min_value: numeric && typeof min === "number" ? min : null,
    max_value: numeric && typeof max === "number" ? max : null,
    categories: values.dataType === "categorical" ? parseCategories(values.categories) : [],
    description: values.description.trim() === "" ? null : values.description.trim(),
  };
}

export function toConfigCreate(values: ConfigFormValues): ScoreConfigCreate {
  return { name: values.name.trim(), data_type: values.dataType, ...typedFields(values) };
}

/** The edit body. The type is sent only when it changed: a new type is refused once scores exist. */
export function toConfigUpdate(values: ConfigFormValues, config: ScoreConfig): ScoreConfigUpdate {
  const fields = typedFields(values);
  return values.dataType === config.data_type ? fields : { ...fields, data_type: values.dataType };
}
