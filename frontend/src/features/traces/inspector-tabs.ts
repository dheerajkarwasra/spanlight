import type { Span } from "@/lib/api";

import { hasModelCall } from "./cost-explanation";

/** The span inspector's tabs, in display order; the value is the URL's `tab=`. */
export const INSPECTOR_TABS = [
  "messages",
  "cost",
  "scores",
  "attributes",
  "raw",
  "replays",
] as const;
export type InspectorTab = (typeof INSPECTOR_TABS)[number];

/** What `resolveTab` reads of a span. */
export type TabSpan = Pick<Span, "kind"> & Parameters<typeof hasModelCall>[0];

/** Values the inspector used before the tabs were renamed; old links still open the right tab. */
const LEGACY_TABS: ReadonlyMap<string, InspectorTab> = new Map([
  ["io", "messages"],
  ["metadata", "attributes"],
]);

/** Cost shows for a span that made (or rolled up) a model call; Replays for LLM spans only. */
export function tabApplies(tab: InspectorTab, span: TabSpan): boolean {
  if (tab === "cost") {
    return span.kind === "llm" || hasModelCall(span);
  }
  return tab !== "replays" || span.kind === "llm";
}

/**
 * The tab to show for `tab` (the URL value, any string) on `span`: Messages for an unknown value
 * or a tab that doesn't apply. The URL is left as it is, so the tab comes back on a span it fits.
 */
export function resolveTab(tab: string | undefined, span: TabSpan): InspectorTab {
  const known = INSPECTOR_TABS.find((candidate) => candidate === tab) ?? LEGACY_TABS.get(tab ?? "");
  return known !== undefined && tabApplies(known, span) ? known : "messages";
}
