import type {
  ChatMessage,
  ModelAdvice,
  PlaygroundConfig,
  PlaygroundParams,
  PromptTemplate,
  PromptVersion,
} from "@/lib/api";
import {
  compileTemplate,
  TEMPLATE_TOO_LARGE_MESSAGE,
  TemplateTooLargeError,
} from "@/lib/prompt-template";

/** The server runs one to three configurations side by side. */
export const MAX_COLUMNS = 3;
export const COLUMN_LETTERS = ["A", "B", "C"] as const;

export type SourceKind = "prompt" | "messages";

/** A prompt version the column resolved: what the request needs and what the form shows. */
export interface PickedVersion {
  promptName: string;
  version: number;
  id: string;
  /** The `{{variables}}` the version's template uses, in order of first use. */
  variables: string[];
  /** The version's template, to check the filled-in prompt's size before a run. */
  template: PromptTemplate;
}

/**
 * One column of the playground. Both sources keep their input, so switching the segmented control
 * back and forth never loses what was typed. Parameters stay text until they are sent, because an
 * empty field means "leave it to the provider", which a number cannot say.
 */
export interface ColumnState {
  key: string;
  sourceKind: SourceKind;
  version: PickedVersion | null;
  variables: Record<string, string>;
  messages: ChatMessage[];
  credentialId: string;
  model: string;
  temperature: string;
  maxTokens: string;
  topP: string;
}

export function newColumn(key: string): ColumnState {
  return {
    key,
    sourceKind: "prompt",
    version: null,
    variables: {},
    messages: [{ role: "user", content: "" }],
    credentialId: "",
    model: "",
    temperature: "",
    maxTokens: "",
    topP: "",
  };
}

/**
 * The column patch for a picked prompt version: the version, one value per variable (keeping
 * what was typed for a name the new version also uses) and the version's model as a default.
 */
export function pickVersion(
  column: ColumnState,
  promptName: string,
  version: PromptVersion,
): Partial<ColumnState> {
  return {
    sourceKind: "prompt",
    version: {
      promptName,
      version: version.version,
      id: version.id,
      variables: version.variables,
      template: version.template,
    },
    variables: Object.fromEntries(
      version.variables.map((name) => [name, column.variables[name] ?? ""]),
    ),
    model: column.model || version.model_config.model || "",
  };
}

/** The id of a column's heading, where focus goes when a neighbouring column is removed. */
export function runHeadingId(key: string): string {
  return `playground-run-${key}`;
}

export function canAddColumn(columns: readonly ColumnState[]): boolean {
  return columns.length < MAX_COLUMNS;
}

/** The columns with one more at the end; unchanged at three. */
export function addColumn(columns: readonly ColumnState[], key: string): ColumnState[] {
  return canAddColumn(columns) ? [...columns, newColumn(key)] : [...columns];
}

/** Removes a column; the last one cannot go, so there is always something to run. */
export function removeColumn(columns: readonly ColumnState[], key: string): ColumnState[] {
  return columns.length > 1 ? columns.filter((column) => column.key !== key) : [...columns];
}

/** A copy of the column right after it, with its own key; unchanged at three. */
export function duplicateColumn(
  columns: readonly ColumnState[],
  key: string,
  copyKey: string,
): ColumnState[] {
  const index = columns.findIndex((column) => column.key === key);
  const source = columns[index];
  if (!source || !canAddColumn(columns)) {
    return [...columns];
  }
  const copy: ColumnState = {
    ...source,
    key: copyKey,
    variables: { ...source.variables },
    messages: source.messages.map((message) => ({ ...message })),
  };
  return [...columns.slice(0, index + 1), copy, ...columns.slice(index + 1)];
}

export function updateColumn(
  columns: readonly ColumnState[],
  key: string,
  patch: Partial<ColumnState>,
): ColumnState[] {
  return columns.map((column) => (column.key === key ? { ...column, ...patch } : column));
}

/** The credential the column uses: its own pick, or the only one the organization has. */
export function effectiveCredentialId(
  column: ColumnState,
  credentialIds: readonly string[],
): string {
  if (column.credentialId) {
    return column.credentialId;
  }
  return credentialIds.length === 1 ? (credentialIds[0] ?? "") : "";
}

interface ParamRule {
  min: number;
  max: number;
  integer: boolean;
}

export const TEMPERATURE_RULE: ParamRule = { min: 0, max: 2, integer: false };
export const MAX_TOKENS_RULE: ParamRule = { min: 1, max: 4096, integer: true };
export const TOP_P_RULE: ParamRule = { min: 0, max: 1, integer: false };

/** `undefined` for an empty field (provider default), `null` for text that is not allowed. */
export function parseParam(text: string, rule: ParamRule): number | null | undefined {
  const trimmed = text.trim();
  if (trimmed === "") {
    return undefined;
  }
  const value = Number(trimmed);
  const valid =
    Number.isFinite(value) &&
    value >= rule.min &&
    value <= rule.max &&
    (!rule.integer || Number.isInteger(value));
  return valid ? value : null;
}

/** Only the parameters the user set: the newest Claude models reject non-default sampling. */
export function toParams(column: ColumnState): PlaygroundParams {
  const params: PlaygroundParams = {};
  const temperature = parseParam(column.temperature, TEMPERATURE_RULE);
  const maxTokens = parseParam(column.maxTokens, MAX_TOKENS_RULE);
  const topP = parseParam(column.topP, TOP_P_RULE);
  if (typeof temperature === "number") params.temperature = temperature;
  if (typeof maxTokens === "number") params.max_tokens = maxTokens;
  if (typeof topP === "number") params.top_p = topP;
  return params;
}

/** The messages that will be sent: the ones with text. */
export function filledMessages(column: ColumnState): ChatMessage[] {
  return column.messages.filter((message) => message.content.trim() !== "");
}

export function missingVariables(column: ColumnState): string[] {
  const version = column.version;
  if (!version) {
    return [];
  }
  // An empty value is allowed (§4.2): only a variable with no value at all is missing.
  return version.variables.filter((name) => !Object.hasOwn(column.variables, name));
}

/** The prompt would pass the server's 256 KB cap once its variables are filled in. */
function tooLarge(template: PromptTemplate, variables: Record<string, string>): boolean {
  try {
    compileTemplate(template, variables);
    return false;
  } catch (error) {
    // Other compile errors (a malformed stored template) are the server's to report.
    return error instanceof TemplateTooLargeError;
  }
}

/** What stops this column from running, as a short sentence; null when it can run. */
export function columnProblem(column: ColumnState, credentialId: string): string | null {
  if (column.sourceKind === "prompt") {
    if (!column.version) return "Pick a prompt version.";
    const missing = missingVariables(column);
    if (missing.length > 0) return `Fill in ${missing.join(", ")}.`;
    if (tooLarge(column.version.template, column.variables)) return TEMPLATE_TOO_LARGE_MESSAGE;
  } else if (filledMessages(column).length === 0) {
    return "Write at least one message.";
  }
  if (!credentialId) return "Choose a credential.";
  if (column.model.trim() === "") return "Enter a model.";
  const invalid = [
    parseParam(column.temperature, TEMPERATURE_RULE) === null ? "Temperature (0 to 2)" : null,
    parseParam(column.maxTokens, MAX_TOKENS_RULE) === null ? "Max tokens (1 to 4096)" : null,
    parseParam(column.topP, TOP_P_RULE) === null ? "Top p (0 to 1)" : null,
  ].filter((name) => name !== null);
  return invalid.length > 0 ? `Check ${invalid.join(", ")}.` : null;
}

/** The request body entry of one column. Call it only when `columnProblem` is null. */
export function toRequestConfig(column: ColumnState, credentialId: string): PlaygroundConfig {
  const version = column.version;
  const source =
    column.sourceKind === "prompt" && version
      ? {
          prompt_version_id: version.id,
          variables: Object.fromEntries(
            version.variables.map((name) => [name, column.variables[name] ?? ""]),
          ),
        }
      : { messages: filledMessages(column) };
  return {
    credential_id: credentialId,
    model: column.model.trim(),
    params: toParams(column),
    source,
  };
}

/** The request body of a run: the columns in order, so results line up with them. */
export function toRequestConfigs(
  columns: readonly ColumnState[],
  credentialIds: readonly string[],
): PlaygroundConfig[] {
  return columns.map((column) =>
    toRequestConfig(column, effectiveCredentialId(column, credentialIds)),
  );
}

/** The first problem across the columns, with the letter of the column that has it. */
export function firstProblem(
  columns: readonly ColumnState[],
  credentialIds: readonly string[],
): { index: number; message: string } | null {
  for (const [index, column] of columns.entries()) {
    const message = columnProblem(column, effectiveCredentialId(column, credentialIds));
    if (message) {
      return { index, message };
    }
  }
  return null;
}

/** The template "Save as version" posts: the column's messages, as a chat prompt. */
export function messagesTemplate(column: ColumnState): PromptTemplate {
  return { kind: "chat", messages: filledMessages(column) };
}

/** The model and parameters of a column as version advice; only what is set. */
export function modelAdvice(column: ColumnState): ModelAdvice {
  const params = toParams(column);
  const advice: ModelAdvice = {};
  if (column.model.trim() !== "") advice.model = column.model.trim();
  if (params.temperature !== undefined) advice.temperature = params.temperature;
  if (params.max_tokens !== undefined) advice.max_tokens = params.max_tokens;
  if (params.top_p !== undefined) advice.top_p = params.top_p;
  return advice;
}
