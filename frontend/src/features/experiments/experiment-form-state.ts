/** The new-experiment form: its state, its checks and the request it builds. Pure. */
import {
  errorMessage,
  isApiError,
  type ChatMessage,
  type ExperimentCreate,
  type ExperimentParams,
  type ExperimentTarget,
  type PromptVersion,
} from "@/lib/api";
import { roleBlockedCopy } from "@/lib/permissions";

import type { NewExperimentSearch } from "./search";

export type TargetSource = "prompt_version" | "messages";

export interface FormState {
  name: string;
  datasetId: string;
  source: TargetSource;
  promptName: string;
  versionId: string;
  messages: ChatMessage[];
  credentialId: string;
  model: string;
  /** Sampling parameters as typed; blank means "leave it to the provider". */
  temperature: string;
  maxTokens: string;
  topP: string;
  evaluatorIds: string[];
  /** Blank means the server default ($5.00 and 1,000 items). */
  maxCost: string;
  maxItems: string;
}

export type FieldName =
  | "name"
  | "datasetId"
  | "versionId"
  | "messages"
  | "credentialId"
  | "model"
  | "temperature"
  | "maxTokens"
  | "topP"
  | "maxCost"
  | "maxItems";

export type FormErrors = Partial<Record<FieldName | "form", string>>;

export const MAX_ITEMS_CEILING = 1_000;
const MONEY = /^\d+(\.\d{1,4})?$/;
export const DEFAULT_MAX_COST = "5.00";

export const INITIAL_STATE: FormState = {
  name: "",
  datasetId: "",
  source: "prompt_version",
  promptName: "",
  versionId: "",
  messages: [
    { role: "system", content: "" },
    { role: "user", content: "" },
  ],
  credentialId: "",
  model: "",
  temperature: "",
  maxTokens: "",
  topP: "",
  evaluatorIds: [],
  maxCost: "",
  maxItems: "",
};

/**
 * What a link to the new-experiment page fills in. The version counts only when it is one of the
 * prompt's `versions` (the picker's list), and then brings its model exactly like picking it does.
 */
export function prefillState(
  search: Pick<NewExperimentSearch, "dataset" | "prompt" | "version">,
  versions: PromptVersion[] | undefined,
): Partial<FormState> {
  const patch: Partial<FormState> = {};
  if (search.dataset) patch.datasetId = search.dataset;
  if (search.prompt) patch.promptName = search.prompt;
  const version = search.prompt ? versions?.find((item) => item.id === search.version) : undefined;
  if (version) {
    patch.source = "prompt_version";
    patch.versionId = version.id;
    if (version.model_config.model) patch.model = version.model_config.model;
  }
  return patch;
}

interface NumberRule {
  min: number;
  max: number;
  integer?: boolean;
  /** The lower bound itself is not allowed (a cost limit must be above zero). */
  exclusiveMin?: boolean;
}

/** A typed number within the rule, or null; blank is `undefined` (not set). */
export function parseNumber(text: string, rule: NumberRule): number | null | undefined {
  const trimmed = text.trim();
  if (trimmed === "") {
    return undefined;
  }
  const value = Number(trimmed);
  const low = rule.exclusiveMin ? value <= rule.min : value < rule.min;
  if (!Number.isFinite(value) || low || value > rule.max) {
    return null;
  }
  return rule.integer && !Number.isInteger(value) ? null : value;
}

const RULES = {
  temperature: { min: 0, max: 2 },
  maxTokens: { min: 1, max: 4096, integer: true },
  topP: { min: 0, max: 1 },
  maxCost: { min: 0, max: 1000, exclusiveMin: true },
  maxItems: { min: 1, max: MAX_ITEMS_CEILING, integer: true },
} satisfies Record<string, NumberRule>;

const RANGE_COPY: Record<keyof typeof RULES, string> = {
  temperature: "Use 0 to 2.",
  maxTokens: "Use a whole number from 1 to 4,096.",
  topP: "Use 0 to 1.",
  maxCost: "Use an amount above $0 and up to $1,000.",
  maxItems: "Use a whole number from 1 to 1,000.",
};

/** Every problem the form can see before it sends anything. */
export function validate(state: FormState): FormErrors {
  const errors: FormErrors = {};
  if (state.name.trim() === "") {
    errors.name = "Name the experiment.";
  } else if (state.name.trim().length > 200) {
    errors.name = "Use 200 characters or fewer.";
  }
  if (state.datasetId === "") {
    errors.datasetId = "Choose a dataset.";
  }
  if (state.source === "prompt_version" && state.versionId === "") {
    errors.versionId = "Choose a prompt and a version.";
  }
  if (state.source === "messages" && !state.messages.some((m) => m.content.trim() !== "")) {
    errors.messages = "Write at least one message.";
  }
  if (state.credentialId === "") {
    errors.credentialId = "Choose a credential.";
  }
  if (state.model.trim() === "") {
    errors.model = "Name the model to call.";
  }
  for (const field of Object.keys(RULES) as (keyof typeof RULES)[]) {
    if (parseNumber(state[field], RULES[field]) === null) {
      errors[field] = RANGE_COPY[field];
    }
  }
  // The server keeps a cost limit to four decimal places and refuses more.
  if (!errors.maxCost && state.maxCost.trim() !== "" && !MONEY.test(state.maxCost.trim())) {
    errors.maxCost = "Use at most four decimal places, like 0.5 or 2.1250.";
  }
  return errors;
}

function params(state: FormState): ExperimentParams {
  const result: ExperimentParams = {};
  const temperature = parseNumber(state.temperature, RULES.temperature);
  const maxTokens = parseNumber(state.maxTokens, RULES.maxTokens);
  const topP = parseNumber(state.topP, RULES.topP);
  // Only what was typed: some models reject a sampling value they were not asked to change.
  if (typeof temperature === "number") result.temperature = temperature;
  if (typeof maxTokens === "number") result.max_tokens = maxTokens;
  if (typeof topP === "number") result.top_p = topP;
  return result;
}

function target(state: FormState): ExperimentTarget {
  const shared = {
    credential_id: state.credentialId,
    model: state.model.trim(),
    params: params(state),
  };
  if (state.source === "prompt_version") {
    return { kind: "prompt_version", prompt_version_id: state.versionId, ...shared };
  }
  const messages = state.messages.filter((message) => message.content.trim() !== "");
  return { kind: "messages", messages, ...shared };
}

/** The create request of a valid form. */
export function toCreate(state: FormState): ExperimentCreate {
  const maxCost = parseNumber(state.maxCost, RULES.maxCost);
  const maxItems = parseNumber(state.maxItems, RULES.maxItems);
  return {
    name: state.name.trim(),
    dataset_id: state.datasetId,
    config: {
      target: target(state),
      ...(typeof maxCost === "number" ? { max_cost_usd: state.maxCost.trim() } : {}),
      ...(typeof maxItems === "number" ? { max_items: maxItems } : {}),
    },
    evaluator_ids: state.evaluatorIds,
  };
}

/** Where a refused create lands: the field it is about, in plain words. */
export function serverErrors(error: unknown): FormErrors {
  if (!isApiError(error)) {
    return { form: errorMessage(error) };
  }
  switch (error.code) {
    case "NAME_TAKEN":
      return { name: "An experiment with this name already exists. Pick another name." };
    case "EXPERIMENT_MODEL_UNPRICED":
      return {
        model:
          "This model has no price, so the cost limit can't be enforced. Pick a priced model or add a price override.",
      };
    case "VALIDATION_ERROR": {
      const field = error.fieldErrors[0]?.field ?? "";
      if (field.includes("max_items")) {
        return { maxItems: "That is more items than this server allows in one run." };
      }
      if (field.includes("max_cost_usd")) {
        return { maxCost: "Use an amount above $0, up to $1,000, with at most four decimals." };
      }
      return { form: "Some settings are not valid. Check the fields and try again." };
    }
    default:
      if (error.status === 404) {
        return {
          form: "The dataset, prompt version, credential or an evaluator no longer exists. Pick again.",
        };
      }
      if (error.status === 403) {
        return { form: roleBlockedCopy("create experiments") };
      }
      return { form: errorMessage(error) };
  }
}
