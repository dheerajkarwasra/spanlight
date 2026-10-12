/*
 * The `{{name}}` template rules, the same as the server's `prompts/templates.py` and the Python
 * SDK. All three are checked against one fixture, `features/prompts/template-cases.json` here.
 * It lives in `lib` because the prompt screens and the playground both read templates.
 *
 * Values are inserted verbatim and never re-expanded. There are no sections, escapes or partials.
 */

export const MAX_TEMPLATE_BYTES = 64 * 1024;
export const MAX_CHAT_MESSAGES = 50;
/**
 * Bound on the rendered output (text, or the sum of every message), in UTF-8 bytes. Without it a
 * small template that repeats a large variable could render hundreds of megabytes.
 */
export const MAX_COMPILED_BYTES = 256 * 1024;

/** The human sentence for a prompt that is too large once its variables are filled in. */
export const TEMPLATE_TOO_LARGE_MESSAGE =
  "With its variables filled in, this prompt is larger than 256 KB. Use shorter variable values.";

const NAME = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/;
// A closed `{{ ... }}` on one line (the inner text may be anything), or a lone `{{` that never closes.
const TOKEN = /\{\{([^\n]*?)\}\}|\{\{/g;

/** A template as it may arrive from anywhere: the shape is checked, not assumed. */
export interface LooseTemplate {
  kind: string;
  text?: string | null;
  messages?: readonly { role: string; content: string }[] | null;
}

export interface TemplatePosition {
  /** 0-based index of the chat message; null for a text template. */
  message: number | null;
  /** 1-based. */
  line: number;
  /** 1-based. */
  column: number;
}

export type InvalidReason = "unclosed" | "invalid-name" | "shape" | "too-large";

/** The template is malformed: bad braces, a bad variable name, the wrong shape or too large. */
export class InvalidTemplateError extends Error {
  readonly reason: InvalidReason;
  /** Where the bad `{{` starts; null for shape and size problems. */
  readonly position: TemplatePosition | null;
  /** The offending `{{…}}` for `invalid-name`. */
  readonly token: string | null;

  constructor(
    reason: InvalidReason,
    message: string,
    position: TemplatePosition | null = null,
    token: string | null = null,
  ) {
    super(message);
    this.name = "InvalidTemplateError";
    this.reason = reason;
    this.position = position;
    this.token = token;
  }
}

/** Compile was called without every variable the template uses. `names` are all of them, sorted. */
export class MissingVariablesError extends Error {
  readonly names: string[];

  constructor(names: string[]) {
    super(`missing variables: ${names.join(", ")}`);
    this.name = "MissingVariablesError";
    this.names = names;
  }
}

/** The rendered output would exceed `MAX_COMPILED_BYTES` (256 KB) with the variables in. */
export class TemplateTooLargeError extends Error {
  constructor() {
    super(`compiled prompt exceeds ${String(MAX_COMPILED_BYTES)} bytes`);
    this.name = "TemplateTooLargeError";
  }
}

export interface CompiledPrompt {
  text: string | null;
  messages: { role: string; content: string }[] | null;
  variablesUsed: string[];
  unusedVariables: string[];
}

// A scanned piece of a template: literal text, or a variable reference when `name` is set.
interface Part {
  literal: string;
  name: string | null;
}

function positionOf(source: string, offset: number, message: number | null): TemplatePosition {
  const before = source.slice(0, offset);
  const line = before.split("\n").length;
  const column = offset - (before.lastIndexOf("\n") + 1) + 1;
  return { message, line, column };
}

function scan(source: string, message: number | null): Part[] {
  const parts: Part[] = [];
  let cursor = 0;
  for (const match of source.matchAll(TOKEN)) {
    const start = match.index;
    if (start > cursor) {
      parts.push({ literal: source.slice(cursor, start), name: null });
    }
    const inner = match[1];
    if (inner === undefined) {
      const position = positionOf(source, start, message);
      throw new InvalidTemplateError("unclosed", "unclosed '{{'", position);
    }
    const name = inner.replace(/^[ \t]+|[ \t]+$/g, "");
    if (!NAME.test(name)) {
      const position = positionOf(source, start, message);
      throw new InvalidTemplateError("invalid-name", "invalid variable name", position, match[0]);
    }
    parts.push({ literal: "", name });
    cursor = start + match[0].length;
  }
  if (cursor < source.length) {
    parts.push({ literal: source.slice(cursor), name: null });
  }
  return parts;
}

const encoder = new TextEncoder();

/** Checks the template's shape and bounds, and returns its text blocks in order. */
function sources(template: LooseTemplate): string[] {
  let blocks: string[];
  if (template.kind === "text") {
    if (typeof template.text !== "string" || template.messages != null) {
      throw new InvalidTemplateError("shape", "a text template needs `text` and no `messages`");
    }
    blocks = [template.text];
  } else if (template.kind === "chat") {
    const messages = template.messages;
    if (messages == null || template.text != null) {
      throw new InvalidTemplateError("shape", "a chat template needs `messages` and no `text`");
    }
    if (messages.length < 1 || messages.length > MAX_CHAT_MESSAGES) {
      throw new InvalidTemplateError("shape", "a chat template needs 1 to 50 messages");
    }
    blocks = messages.map((message) => message.content);
  } else {
    throw new InvalidTemplateError("shape", "unknown template kind");
  }
  const bytes = blocks.reduce((total, block) => total + encoder.encode(block).length, 0);
  if (bytes > MAX_TEMPLATE_BYTES) {
    throw new InvalidTemplateError("too-large", "template exceeds 64 KB");
  }
  return blocks;
}

function scanAll(template: LooseTemplate): Part[][] {
  const isChat = template.kind === "chat";
  return sources(template).map((block, index) => scan(block, isChat ? index : null));
}

function orderedUnique(partsPerBlock: Part[][]): string[] {
  const seen = new Set<string>();
  for (const parts of partsPerBlock) {
    for (const part of parts) {
      if (part.name !== null) {
        seen.add(part.name);
      }
    }
  }
  return [...seen];
}

/** Variable names in first-appearance order, de-duplicated. Throws `InvalidTemplateError`. */
export function extractVariables(template: LooseTemplate): string[] {
  return orderedUnique(scanAll(template));
}

/** Throws `TemplateTooLargeError` before rendering when the output would pass the cap. */
function checkSize(partsPerBlock: Part[][], variables: Readonly<Record<string, string>>): void {
  const sizes = new Map<string, number>();
  let total = 0;
  for (const parts of partsPerBlock) {
    for (const part of parts) {
      if (part.name === null) {
        total += encoder.encode(part.literal).length;
      } else {
        let size = sizes.get(part.name);
        if (size === undefined) {
          size = encoder.encode(variables[part.name] ?? "").length;
          sizes.set(part.name, size);
        }
        total += size;
      }
      if (total > MAX_COMPILED_BYTES) {
        throw new TemplateTooLargeError();
      }
    }
  }
}

function render(parts: Part[], variables: Readonly<Record<string, string>>): string {
  return parts.map((part) => (part.name === null ? part.literal : variables[part.name])).join("");
}

/**
 * Substitutes `variables` into the template. Extra variables are reported, not rejected.
 * Throws `InvalidTemplateError`, `MissingVariablesError`, or `TemplateTooLargeError` when the
 * rendered output would exceed `MAX_COMPILED_BYTES` (256 KB, text or all messages together).
 */
export function compileTemplate(
  template: LooseTemplate,
  variables: Readonly<Record<string, string>>,
): CompiledPrompt {
  const scanned = scanAll(template);
  const used = orderedUnique(scanned);
  const missing = used.filter((name) => !Object.hasOwn(variables, name)).sort();
  if (missing.length > 0) {
    throw new MissingVariablesError(missing);
  }
  const unused = Object.keys(variables)
    .filter((name) => !used.includes(name))
    .sort();
  checkSize(scanned, variables);
  if (template.kind === "text") {
    return {
      text: render(scanned[0] ?? [], variables),
      messages: null,
      variablesUsed: used,
      unusedVariables: unused,
    };
  }
  const messages = (template.messages ?? []).map((message, index) => ({
    role: message.role,
    content: render(scanned[index] ?? [], variables),
  }));
  return { text: null, messages, variablesUsed: used, unusedVariables: unused };
}

/** The editor's sentence for a malformed template, e.g. `Line 1: “{{” has no closing “}}”.` */
export function describeTemplateError(error: InvalidTemplateError): string {
  if (error.reason === "too-large") {
    return "The template is larger than 64 KB. Shorten it to save.";
  }
  if (error.reason === "shape") {
    return "A chat prompt needs between 1 and 50 messages.";
  }
  const where = error.position === null ? "" : locationLabel(error.position);
  if (error.reason === "unclosed") {
    return `${where}“{{” has no closing “}}”.`;
  }
  return `${where}“${error.token ?? "{{}}"}” is not a valid variable name. Use letters, digits and _, starting with a letter or _.`;
}

/**
 * The sentence for any compile failure, for a preview or a run: a malformed template, missing
 * variables, or a prompt too large once filled in. Null for an error that is not a compile error.
 */
export function describeCompileError(error: unknown): string | null {
  if (error instanceof TemplateTooLargeError) {
    return TEMPLATE_TOO_LARGE_MESSAGE;
  }
  if (error instanceof MissingVariablesError) {
    const several = error.names.length > 1;
    return `Fill in the ${several ? "variables" : "variable"} ${error.names.join(", ")}.`;
  }
  if (error instanceof InvalidTemplateError) {
    return describeTemplateError(error);
  }
  return null;
}

function locationLabel(position: TemplatePosition): string {
  const line = `line ${String(position.line)}`;
  if (position.message === null) {
    return `Line ${String(position.line)}: `;
  }
  return `Message ${String(position.message + 1)}, ${line}: `;
}

const VARIABLE_SPLIT = /(\{\{[ \t]*[A-Za-z_][A-Za-z0-9_]{0,63}[ \t]*\}\})/;

/** Text pieces for display, each marked when it is a `{{name}}` reference (shown in violet). */
export function highlightParts(text: string): { text: string; variable: boolean }[] {
  return text
    .split(VARIABLE_SPLIT)
    .filter((piece) => piece !== "")
    .map((piece) => ({ text: piece, variable: VARIABLE_SPLIT.test(piece) }));
}

/** Variables of a template, or the problem that stops it from being read. Never throws. */
export function inspectTemplate(
  template: LooseTemplate,
): { variables: string[]; problem: null } | { variables: null; problem: InvalidTemplateError } {
  try {
    return { variables: extractVariables(template), problem: null };
  } catch (error) {
    if (error instanceof InvalidTemplateError) {
      return { variables: null, problem: error };
    }
    throw error;
  }
}
