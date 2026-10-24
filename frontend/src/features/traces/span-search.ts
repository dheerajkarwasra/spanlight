/** Find span and the kind filter (spec D7): which rows match, and which ancestors stay. */
import type { Span } from "@/lib/api";

import type { SpanNode } from "./span-tree";

/** Characters of text searched per payload; big payloads are cut, not scanned whole. */
const PAYLOAD_SEARCH_CHARS = 20_000;

/** The payload's string values joined, up to the cap: what a person reads, not its JSON. */
function payloadText(payload: unknown): string {
  const parts: string[] = [];
  let length = 0;
  const stack: unknown[] = [payload];
  while (stack.length > 0 && length < PAYLOAD_SEARCH_CHARS) {
    const value = stack.pop();
    if (typeof value === "string") {
      parts.push(value);
      length += value.length + 1;
    } else if (value !== null && typeof value === "object") {
      stack.push(...(Object.values(value) as unknown[]).reverse());
    }
  }
  return parts.join("\n").slice(0, PAYLOAD_SEARCH_CHARS);
}

// Spans are immutable query data, so each one's lower-cased text is built once.
const haystacks = new WeakMap<Span, string>();

function haystack(span: Span): string {
  let text = haystacks.get(span);
  if (text === undefined) {
    const payloads = span.payload_omitted ? [] : [span.input, span.output].map(payloadText);
    text = [span.name, ...payloads].join("\n").toLowerCase();
    haystacks.set(span, text);
  }
  return text;
}

/** Spans whose name, or loaded input/output, contains `query` (case-insensitive). */
export function matchSpans(spans: readonly Span[], query: string): Set<string> {
  const needle = query.trim().toLowerCase();
  if (needle === "") {
    return new Set();
  }
  return new Set(spans.filter((span) => haystack(span).includes(needle)).map((s) => s.span_id));
}

/** The forest cut down to spans that pass `keep`, plus their ancestors as context. */
export function filterTree(roots: readonly SpanNode[], keep: (span: Span) => boolean): SpanNode[] {
  return roots.flatMap((node) => {
    const children = filterTree(node.children, keep);
    return keep(node.span) || children.length > 0 ? [{ ...node, children }] : [];
  });
}
