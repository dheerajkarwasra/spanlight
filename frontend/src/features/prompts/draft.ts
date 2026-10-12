import type {
  ChatMessage,
  ChatRole,
  ModelAdvice,
  PromptKind,
  PromptTemplate,
  PromptVersion,
} from "@/lib/api";

/** What the editor holds: the template plus the model advice as typed (numbers stay strings). */
export interface Draft {
  template: PromptTemplate;
  model: string;
  temperature: string;
  maxTokens: string;
  topP: string;
  commitMessage: string;
}

export interface AdviceErrors {
  temperature?: string;
  maxTokens?: string;
  topP?: string;
}

export const MAX_COMMIT_MESSAGE = 500;
export const MAX_DESCRIPTION = 1000;
export const MAX_PROMPT_NAME = 128;
export const CHAT_ROLES: readonly ChatRole[] = ["system", "user", "assistant"];

const PROMPT_NAME = /^[A-Za-z0-9][A-Za-z0-9_.-]*(\/[A-Za-z0-9][A-Za-z0-9_.-]*)*$/;
const RESERVED_SEGMENTS = new Set(["versions", "labels"]);
const LABEL_NAME = /^[a-z0-9][a-z0-9_-]{0,31}$/;

export function emptyTemplate(kind: PromptKind): PromptTemplate {
  return kind === "text"
    ? { kind: "text", text: "" }
    : {
        kind: "chat",
        messages: [
          { role: "system", content: "" },
          { role: "user", content: "" },
        ],
      };
}

function numberText(value: number | null | undefined): string {
  return value === null || value === undefined ? "" : String(value);
}

export function emptyDraft(kind: PromptKind): Draft {
  return {
    template: emptyTemplate(kind),
    model: "",
    temperature: "",
    maxTokens: "",
    topP: "",
    commitMessage: "Initial version",
  };
}

/** A draft that starts as a copy of `version`, with an empty commit message. */
export function draftFromVersion(version: PromptVersion): Draft {
  const advice = version.model_config;
  return {
    template: version.template,
    model: advice.model ?? "",
    temperature: numberText(advice.temperature),
    maxTokens: numberText(advice.max_tokens),
    topP: numberText(advice.top_p),
    commitMessage: "",
  };
}

function parseNumber(text: string, min: number, max: number, integer: boolean): number | null {
  const trimmed = text.trim();
  if (trimmed === "") {
    return null;
  }
  const value = Number(trimmed);
  if (!Number.isFinite(value) || value < min || value > max) {
    return Number.NaN;
  }
  return integer && !Number.isInteger(value) ? Number.NaN : value;
}

/** The advice to send, leaving out what is unset, or the field errors that stop it. */
export function toAdvice(draft: Draft): { advice: ModelAdvice; errors: AdviceErrors } {
  const temperature = parseNumber(draft.temperature, 0, 2, false);
  const maxTokens = parseNumber(draft.maxTokens, 1, 10_000_000, true);
  const topP = parseNumber(draft.topP, 0, 1, false);
  const errors: AdviceErrors = {};
  if (Number.isNaN(temperature)) {
    errors.temperature = "Enter a number from 0 to 2.";
  }
  if (Number.isNaN(maxTokens)) {
    errors.maxTokens = "Enter a whole number from 1.";
  }
  if (Number.isNaN(topP)) {
    errors.topP = "Enter a number from 0 to 1.";
  }
  const advice: ModelAdvice = {};
  const model = draft.model.trim();
  if (model !== "") {
    advice.model = model;
  }
  if (temperature !== null && !Number.isNaN(temperature)) {
    advice.temperature = temperature;
  }
  if (maxTokens !== null && !Number.isNaN(maxTokens)) {
    advice.max_tokens = maxTokens;
  }
  if (topP !== null && !Number.isNaN(topP)) {
    advice.top_p = topP;
  }
  return { advice, errors };
}

function sameMessages(a: readonly ChatMessage[], b: readonly ChatMessage[]): boolean {
  return (
    a.length === b.length &&
    a.every((message, index) => {
      const other = b[index];
      return (
        other !== undefined && message.role === other.role && message.content === other.content
      );
    })
  );
}

export function sameTemplate(a: PromptTemplate, b: PromptTemplate): boolean {
  if (a.kind === "text" && b.kind === "text") {
    return a.text === b.text;
  }
  if (a.kind === "chat" && b.kind === "chat") {
    return sameMessages(a.messages, b.messages);
  }
  return false;
}

function sameAdvice(a: ModelAdvice, b: ModelAdvice): boolean {
  const keys: (keyof ModelAdvice)[] = ["model", "temperature", "max_tokens", "top_p"];
  return keys.every((key) => (a[key] ?? null) === (b[key] ?? null));
}

/** Whether the draft would make a different version than `base`: Save stays off until it does. */
export function hasChanges(draft: Draft, base: PromptVersion): boolean {
  return (
    !sameTemplate(draft.template, base.template) ||
    !sameAdvice(toAdvice(draft).advice, base.model_config)
  );
}

/** Which chat messages differ from the base version at the same position ("Edited"). */
export function editedMessages(template: PromptTemplate, base: PromptTemplate | null): boolean[] {
  if (template.kind !== "chat") {
    return [];
  }
  const baseMessages = base?.kind === "chat" ? base.messages : [];
  return template.messages.map((message, index) => {
    const original = baseMessages[index];
    return (
      base !== null &&
      original !== undefined &&
      (original.role !== message.role || original.content !== message.content)
    );
  });
}

/** Why a new prompt name is refused, or null when it is valid. */
export function promptNameProblem(name: string): string | null {
  if (name === "") {
    return "Enter a name.";
  }
  if (name.length > MAX_PROMPT_NAME) {
    return `Use at most ${String(MAX_PROMPT_NAME)} characters.`;
  }
  if (!PROMPT_NAME.test(name)) {
    return "Use letters, numbers and / . _ -, with each part between slashes starting with a letter or number.";
  }
  if (name.split("/").some((segment) => RESERVED_SEGMENTS.has(segment))) {
    return "A part of the name can't be “versions” or “labels”.";
  }
  return null;
}

export type LabelProblem = "reserved" | "empty" | "pattern";

/** Why a label name is refused, or null when it is valid. `latest` is reserved. */
export function labelProblem(label: string): LabelProblem | null {
  if (label === "") {
    return "empty";
  }
  if (label === "latest") {
    return "reserved";
  }
  return LABEL_NAME.test(label) ? null : "pattern";
}
