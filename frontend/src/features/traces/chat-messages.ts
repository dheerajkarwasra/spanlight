/**
 * Recognises LLM chat payloads captured on spans and normalises them into one
 * shape the UI can render. Pure functions only — no React here.
 *
 * Supported shapes:
 * - OpenAI Chat Completions: `[{ role, content, tool_calls?, tool_call_id? }]`,
 *   content as a string or `[{ type: "text" | "image_url" | ... }]`, and the
 *   response `{ choices: [{ message }] }`.
 * - OpenAI Responses API: `{ output: [{ type: "message" | "function_call", ... }] }`.
 * - Anthropic Messages: `{ system?, messages: [...] }` with content blocks
 *   (`text`, `image`, `tool_use`, `tool_result`), and the response
 *   `{ role: "assistant", content: [...] }`.
 * - OpenTelemetry GenAI messages: `[{ role, parts: [{ type: "text", content }] }]`.
 * - A single `{ role, content }` message, or a `{ messages: [...] }` wrapper.
 * - A bare content-block list without `role` (an Anthropic response's `content`).
 *
 * Anything else returns `null` so the caller can fall back to a JSON view.
 */

export type ChatRole = "system" | "user" | "assistant" | "tool";

export type ChatPart =
  | { type: "text"; text: string }
  /** `src` is set for inline `data:image/…` images only; they render as thumbnails. */
  | { type: "image"; description: string; src?: string }
  | { type: "tool_call"; id: string | null; name: string; arguments: unknown }
  | { type: "tool_result"; toolCallId: string | null; content: unknown; isError: boolean }
  | { type: "thinking"; text: string }
  | { type: "redacted_thinking" }
  | { type: "json"; value: unknown };

export interface ChatMessage {
  role: ChatRole;
  /** Optional participant or tool name (`name` on OpenAI messages). */
  name: string | null;
  /** Set on `role: "tool"` messages that answer a tool call. */
  toolCallId: string | null;
  parts: ChatPart[];
}

export const ROLE_LABELS: Record<ChatRole, string> = {
  system: "System",
  user: "User",
  assistant: "Assistant",
  tool: "Tool",
};

type JsonObject = Record<string, unknown>;

function isObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringOrNull(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

const ROLE_ALIASES: Record<string, ChatRole> = {
  system: "system",
  developer: "system",
  user: "user",
  human: "user",
  assistant: "assistant",
  model: "assistant",
  ai: "assistant",
  tool: "tool",
  function: "tool",
  ipython: "tool",
};

function normaliseRole(value: unknown): ChatRole | null {
  if (typeof value !== "string") {
    return null;
  }
  return ROLE_ALIASES[value.toLowerCase()] ?? null;
}

/** Tool-call arguments are usually a JSON string; show them as JSON when they parse. */
function parseArguments(value: unknown): unknown {
  if (typeof value !== "string") {
    return value ?? null;
  }
  try {
    return JSON.parse(value) as unknown;
  } catch {
    return value;
  }
}

/** The `data:image/…` URL of an inline image, so it can render as a thumbnail. */
function inlineImageSrc(source: unknown): string | undefined {
  if (typeof source === "string") {
    return source.startsWith("data:image/") ? source : undefined;
  }
  if (!isObject(source)) {
    return undefined;
  }
  if (source.url !== undefined) {
    return inlineImageSrc(source.url);
  }
  const mediaType = stringOrNull(source.media_type);
  const data = stringOrNull(source.data);
  return source.type === "base64" && mediaType?.startsWith("image/") && data
    ? `data:${mediaType};base64,${data}`
    : undefined;
}

function imagePart(source: unknown): ChatPart {
  const src = inlineImageSrc(source);
  const description = describeImage(source);
  return src === undefined ? { type: "image", description } : { type: "image", description, src };
}

function describeImage(source: unknown): string {
  if (typeof source === "string") {
    return source.startsWith("data:") ? "Inline image (base64)" : source;
  }
  if (isObject(source)) {
    const url = stringOrNull(source.url);
    if (url !== null) {
      return describeImage(url);
    }
    const mediaType = stringOrNull(source.media_type);
    const sourceType = stringOrNull(source.type);
    if (sourceType === "base64") {
      return mediaType ? `Inline image (${mediaType}, base64)` : "Inline image (base64)";
    }
    if (mediaType) {
      return `Image (${mediaType})`;
    }
  }
  return "Image";
}

/** Anthropic tool_result content may be a string or a list of text blocks. */
function flattenToolResultContent(content: unknown): unknown {
  if (Array.isArray(content)) {
    const texts: string[] = [];
    for (const block of content) {
      if (isObject(block) && block.type === "text" && typeof block.text === "string") {
        texts.push(block.text);
      } else {
        return content;
      }
    }
    return texts.join("\n\n");
  }
  return content ?? null;
}

function parsePart(part: unknown): ChatPart {
  if (typeof part === "string") {
    return { type: "text", text: part };
  }
  if (!isObject(part)) {
    return { type: "json", value: part };
  }

  switch (part.type) {
    case "text":
    case "input_text":
    case "output_text": {
      // OpenAI/Anthropic use `text`; OpenTelemetry GenAI parts use `content`.
      const text = stringOrNull(part.text) ?? stringOrNull(part.content);
      return text === null ? { type: "json", value: part } : { type: "text", text };
    }
    case "refusal": {
      const refusal = stringOrNull(part.refusal);
      return refusal === null ? { type: "json", value: part } : { type: "text", text: refusal };
    }
    case "image_url":
    case "input_image":
      return imagePart(part.image_url);
    case "image":
      return imagePart(part.source ?? part.url);
    case "tool_use":
    case "tool_call":
    case "function_call":
      return {
        type: "tool_call",
        id: stringOrNull(part.id) ?? stringOrNull(part.call_id),
        name: stringOrNull(part.name) ?? "tool",
        arguments: parseArguments(part.input ?? part.arguments),
      };
    case "tool_result":
    case "tool_call_response":
      return {
        type: "tool_result",
        toolCallId: stringOrNull(part.tool_use_id) ?? stringOrNull(part.id),
        content: flattenToolResultContent(part.content ?? part.response),
        isError: part.is_error === true,
      };
    case "thinking": {
      // The signature is an opaque base64 blob; only the reasoning text is worth showing.
      const text = stringOrNull(part.thinking);
      return text === null ? { type: "json", value: part } : { type: "thinking", text };
    }
    case "redacted_thinking":
      return { type: "redacted_thinking" };
    default:
      return { type: "json", value: part };
  }
}

function parseContent(content: unknown): ChatPart[] | null {
  if (content === null || content === undefined) {
    return [];
  }
  if (typeof content === "string") {
    return content === "" ? [] : [{ type: "text", text: content }];
  }
  if (Array.isArray(content)) {
    return content.filter((block) => !isEmptyThinking(block)).map(parsePart);
  }
  return null;
}

/** Anthropic returns a thinking block with empty text and only a signature by default. */
function isEmptyThinking(block: unknown): boolean {
  return (
    isObject(block) &&
    block.type === "thinking" &&
    (typeof block.thinking !== "string" || block.thinking.trim() === "")
  );
}

function parseOpenAiToolCalls(toolCalls: unknown): ChatPart[] {
  if (!Array.isArray(toolCalls)) {
    return [];
  }
  return toolCalls.map((call): ChatPart => {
    if (!isObject(call)) {
      return { type: "json", value: call };
    }
    const fn = isObject(call.function) ? call.function : {};
    return {
      type: "tool_call",
      id: stringOrNull(call.id),
      name: stringOrNull(fn.name) ?? stringOrNull(call.name) ?? "tool",
      arguments: parseArguments(fn.arguments ?? call.arguments),
    };
  });
}

/** Parses one message object; null when it doesn't look like a chat message. */
export function parseMessage(value: unknown, defaultRole?: ChatRole): ChatMessage | null {
  if (!isObject(value)) {
    return null;
  }
  const role = value.role === undefined ? (defaultRole ?? null) : normaliseRole(value.role);
  if (role === null) {
    return null;
  }

  // OpenTelemetry GenAI messages carry `parts`; everyone else uses `content`.
  const hasParts = Array.isArray(value.parts) && value.content === undefined;
  const hasBody =
    hasParts || "content" in value || "tool_calls" in value || "function_call" in value;
  if (!hasBody) {
    return null;
  }
  const parts = hasParts ? parseContent(value.parts) : parseContent(value.content);
  if (parts === null) {
    return null;
  }

  parts.push(...parseOpenAiToolCalls(value.tool_calls));
  if (isObject(value.function_call)) {
    parts.push({
      type: "tool_call",
      id: null,
      name: stringOrNull(value.function_call.name) ?? "function",
      arguments: parseArguments(value.function_call.arguments),
    });
  }

  return {
    role,
    name: stringOrNull(value.name),
    toolCallId: stringOrNull(value.tool_call_id),
    parts,
  };
}

function parseMessageList(values: unknown[], defaultRole?: ChatRole): ChatMessage[] | null {
  if (values.length === 0) {
    return null;
  }
  const messages: ChatMessage[] = [];
  for (const value of values) {
    const message = parseMessage(value, defaultRole);
    if (message === null) {
      return null;
    }
    messages.push(message);
  }
  return messages;
}

/** Anthropic `system` is a string or a list of text blocks. */
function parseSystemPrompt(system: unknown): ChatMessage | null {
  const parts = parseContent(system);
  if (parts === null || parts.length === 0) {
    return null;
  }
  return { role: "system", name: null, toolCallId: null, parts };
}

/** OpenAI Responses API `output` items. */
function parseResponseOutput(items: unknown[]): ChatMessage[] | null {
  const messages: ChatMessage[] = [];
  for (const item of items) {
    if (!isObject(item)) {
      return null;
    }
    if (item.type === "message") {
      const message = parseMessage(item, "assistant");
      if (message === null) {
        return null;
      }
      messages.push(message);
    } else if (item.type === "function_call") {
      messages.push({
        role: "assistant",
        name: null,
        toolCallId: null,
        parts: [parsePart(item)],
      });
    } else if (item.type === "reasoning") {
      continue;
    } else {
      return null;
    }
  }
  return messages.length > 0 ? messages : null;
}

/** `[{ type: "text", ... }, ...]` — every item is a typed content block. */
function isContentBlockList(value: unknown): value is JsonObject[] {
  return (
    Array.isArray(value) &&
    value.length > 0 &&
    value.every((block) => isObject(block) && typeof block.type === "string")
  );
}

/**
 * A bare content-block list (`[{ type: "thinking" }, { type: "text" }]`, no `role`) is one
 * assistant turn, but only when every block is a kind we render, so a list of typed records
 * (retrieved documents, say) stays JSON.
 */
function parseBlockListMessage(payload: unknown[]): ChatMessage[] | null {
  if (!isContentBlockList(payload)) {
    return null;
  }
  const message = parseMessage({ content: payload }, "assistant");
  return message && message.parts.length > 0 && message.parts.every((part) => part.type !== "json")
    ? [message]
    : null;
}

/**
 * Normalises a span input/output payload into chat messages, or returns null
 * when the payload isn't a recognised chat format.
 */
export function parseChatPayload(payload: unknown): ChatMessage[] | null {
  if (Array.isArray(payload)) {
    return parseMessageList(payload) ?? parseBlockListMessage(payload);
  }
  if (!isObject(payload)) {
    return null;
  }

  // `{ system?, messages: [...] }` — Anthropic request or a generic wrapper.
  if (Array.isArray(payload.messages)) {
    const messages = parseMessageList(payload.messages);
    if (messages === null) {
      return null;
    }
    const system = parseSystemPrompt(payload.system);
    return system ? [system, ...messages] : messages;
  }

  // OpenAI Chat Completions response.
  if (Array.isArray(payload.choices)) {
    const messages: ChatMessage[] = [];
    for (const choice of payload.choices) {
      const message = isObject(choice) ? parseMessage(choice.message, "assistant") : null;
      if (message === null) {
        return null;
      }
      messages.push(message);
    }
    return messages.length > 0 ? messages : null;
  }

  // OpenAI Responses API response.
  if (Array.isArray(payload.output)) {
    return parseResponseOutput(payload.output);
  }

  // A single `{ role, content }` message, or an Anthropic response
  // `{ type: "message", role: "assistant", content: [...] }`.
  if (payload.role !== undefined || payload.type === "message") {
    const message = parseMessage(payload, "assistant");
    return message ? [message] : null;
  }

  // A bare Anthropic response body: `{ content: [{ type: "text", text }], ... }`.
  if (isContentBlockList(payload.content)) {
    const message = parseMessage({ content: payload.content }, "assistant");
    return message ? [message] : null;
  }

  return null;
}

/** All text parts of a message joined by blank lines; null when it has none. */
export function messageText(message: ChatMessage): string | null {
  const texts = message.parts
    .filter((part): part is Extract<ChatPart, { type: "text" }> => part.type === "text")
    .map((part) => part.text.trim())
    .filter((text) => text !== "");
  return texts.length > 0 ? texts.join("\n\n") : null;
}

/** The text shown by "Copy" for any payload: strings verbatim, everything else as JSON. */
export function payloadToClipboardText(payload: unknown): string {
  if (typeof payload === "string") {
    return payload;
  }
  if (payload === undefined) {
    return "null";
  }
  return JSON.stringify(payload, null, 2);
}
