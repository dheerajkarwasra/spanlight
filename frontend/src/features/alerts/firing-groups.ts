import type { AlertEvent, AlertEventRuleKind } from "@/lib/api";

export interface FiringGroup {
  ruleId: string;
  ruleName: string;
  ruleKind: AlertEventRuleKind;
  count: number;
  /** "3", or "50+" while older pages may hold more firings. */
  countLabel: string;
  /** The latest firing's start. */
  lastAt: string;
  /** One of its events is still open. */
  firing: boolean;
  /** Newest first. */
  events: AlertEvent[];
}

/**
 * The loaded events (newest first, as the API sends them) as one row per rule, ordered by each
 * rule's latest firing. Counts are of loaded events: with `hasMore`, any rule may have older
 * firings on the pages not loaded yet, so every count is open-ended.
 */
export function groupFirings(events: readonly AlertEvent[], hasMore: boolean): FiringGroup[] {
  const groups = new Map<string, FiringGroup>();
  for (const event of events) {
    const group = groups.get(event.rule_id);
    if (group) {
      group.events.push(event);
      group.count += 1;
      group.firing ||= event.resolved_at === null;
    } else {
      groups.set(event.rule_id, {
        ruleId: event.rule_id,
        ruleName: event.rule_name,
        ruleKind: event.rule_kind,
        count: 1,
        countLabel: "",
        lastAt: event.started_at,
        firing: event.resolved_at === null,
        events: [event],
      });
    }
  }
  return [...groups.values()].map((group) => ({
    ...group,
    countLabel: `${group.count}${hasMore ? "+" : ""}`,
  }));
}
