/**
 * Best-effort conversion of any JavaScript value into JSON-compatible data.
 *
 * Captured inputs and outputs can be anything: provider responses, class instances, cycles,
 * huge strings. The result holds only plain objects, arrays, strings, finite numbers, booleans
 * and null, with the same bounds as the Python SDK so one span never becomes a huge payload.
 * It never throws.
 */

const MAX_DEPTH = 12;
const MAX_STRING_LENGTH = 32_000;
const MAX_ITEMS = 1_000;
const TRUNCATION_MARKER = "...[truncated]";

interface State {
  cut: boolean;
  ancestors: WeakSet<object>;
}

/** The JSON-compatible form of `value`, and whether anything was cut to fit the bounds. */
export function toJsonable(value: unknown): { value: unknown; cut: boolean } {
  const state: State = { cut: false, ancestors: new WeakSet() };
  try {
    return { value: convert(value, 0, state), cut: state.cut };
  } catch {
    return { value: truncate(safeString(value), state), cut: state.cut };
  }
}

function convert(value: unknown, depth: number, state: State): unknown {
  switch (typeof value) {
    case "undefined":
      return null;
    case "boolean":
      return value;
    case "number":
      return Number.isFinite(value) ? value : String(value);
    case "bigint":
      return value.toString();
    case "string":
      return truncate(value, state);
    case "symbol":
      return value.toString();
    case "function":
      return `[function ${value.name || "anonymous"}]`;
  }
  if (value === null) return null;
  const object = value as object;
  if (depth >= MAX_DEPTH) {
    state.cut = true;
    return "[max depth reached]";
  }
  if (state.ancestors.has(object)) return "[circular]";
  if (object instanceof Date)
    return Number.isNaN(object.getTime()) ? "Invalid Date" : object.toISOString();
  if (object instanceof Error)
    return { type: object.name, message: truncate(object.message, state) };
  if (object instanceof ArrayBuffer || ArrayBuffer.isView(object))
    return `<${object.byteLength} bytes>`;
  const toJSON = (object as { toJSON?: unknown }).toJSON;
  if (typeof toJSON === "function") return convert(toJSON.call(object), depth + 1, state);

  state.ancestors.add(object);
  try {
    if (Array.isArray(object) || object instanceof Set)
      return convertItems([...object], depth, state);
    const entries = object instanceof Map ? [...object.entries()] : Object.entries(object);
    return convertEntries(entries, depth, state);
  } finally {
    state.ancestors.delete(object);
  }
}

function convertItems(items: unknown[], depth: number, state: State): unknown[] {
  const converted = items.slice(0, MAX_ITEMS).map((item) => convert(item, depth + 1, state));
  if (items.length > MAX_ITEMS) {
    converted.push(`... ${items.length - MAX_ITEMS} more items truncated`);
    state.cut = true;
  }
  return converted;
}

function convertEntries(
  entries: [unknown, unknown][],
  depth: number,
  state: State,
): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (const [index, [key, item]] of entries.entries()) {
    if (index >= MAX_ITEMS) {
      result["..."] = `${entries.length - MAX_ITEMS} more keys truncated`;
      state.cut = true;
      break;
    }
    // Like JSON.stringify, an undefined property is left out rather than sent as null.
    if (item !== undefined) result[String(key)] = convert(item, depth + 1, state);
  }
  return result;
}

function truncate(value: string, state: State): string {
  if (value.length <= MAX_STRING_LENGTH) return value;
  state.cut = true;
  return value.slice(0, MAX_STRING_LENGTH) + TRUNCATION_MARKER;
}

function safeString(value: unknown): string {
  try {
    return String(value);
  } catch {
    return "<unrepresentable value>";
  }
}
