/**
 * How dataset values (any JSON) read as text and come back from an editor. A string stays a
 * string; an object or array is JSON. Pure.
 */

export const MAX_PAYLOAD_BYTES = 64 * 1024;
export const MAX_METADATA_BYTES = 8 * 1024;

/** One line of text for a table cell: the string itself, or compact JSON. */
export function valueText(value: unknown): string {
  if (typeof value === "string") return value;
  if (value === null || value === undefined) return "";
  return JSON.stringify(value);
}

/** Text for a textarea: the string itself, or indented JSON; empty for no value. */
export function editorText(value: unknown): string {
  if (typeof value === "string") return value;
  if (value === null || value === undefined) return "";
  return JSON.stringify(value, null, 2);
}

/**
 * The value an editor's text stands for: null when blank, the object or array when the text is
 * one in JSON, else the text itself (the rule CSV uploads follow).
 */
export function parseEditorText(text: string): unknown {
  const stripped = text.trim();
  if (!stripped) return null;
  if (stripped.startsWith("{") || stripped.startsWith("[")) {
    try {
      const value: unknown = JSON.parse(stripped);
      if (typeof value === "object" && value !== null) return value;
    } catch {
      // Kept as text.
    }
  }
  return text;
}

/** The bytes a value takes as compact JSON, as the server measures it. */
export function jsonBytes(value: unknown): number {
  if (value === null || value === undefined) return 0;
  return new TextEncoder().encode(JSON.stringify(value)).length;
}

/** "0.1 KB of 64 KB". */
export function sizeLabel(bytes: number, limit: number): string {
  return `${(bytes / 1024).toFixed(1)} KB of ${String(limit / 1024)} KB`;
}

/** "key: value" for a metadata chip; non-string values as JSON. */
export function metadataChip(key: string, value: unknown): string {
  return `${key}: ${valueText(value)}`;
}

/** Why the text can't be saved: JSON that doesn't parse, or over 64 KB. Null when it can. */
export function payloadProblem(text: string): string | null {
  const stripped = text.trim();
  if (stripped.startsWith("{") || stripped.startsWith("[")) {
    try {
      JSON.parse(stripped);
    } catch {
      return "This starts like JSON but isn't valid JSON. Fix it to save; it would otherwise become plain text.";
    }
  }
  if (jsonBytes(parseEditorText(text)) > MAX_PAYLOAD_BYTES) {
    return "This is larger than 64 KB. Shorten it to save.";
  }
  return null;
}
