import type { AlertRule, Budget, Insight } from "@/lib/api";
import { isFiring } from "@/features/alerts";
import { formatCost, formatRelativeTime, parseMoney } from "@/lib/format";

export type AttentionSeverity = "critical" | "warning" | "info";

export type AttentionItem = {
  id: string;
  title: string;
  meta: string;
  severity: AttentionSeverity;
} & (
  | { kind: "finding"; to: "/$orgId/$projectId/doctor/$insightId"; params: { insightId: string } }
  | { kind: "alert"; to: "/$orgId/$projectId/alerts/$ruleId"; params: { ruleId: string } }
  | { kind: "budget"; to: "/$orgId/$projectId/budgets"; params: Record<string, never> }
);

const MAX_ITEMS = 6;
/** Findings shown, after sorting the fetched ones by severity. */
const MAX_FINDINGS = 3;
/** A budget joins the list from 80 % of its amount (the budget-use bar's warning tick). */
export const BUDGET_WARNING_RATIO = 0.8;

const SEVERITY_ORDER: Record<AttentionSeverity, number> = { critical: 0, warning: 1, info: 2 };

/** Spent over amount for the current period; null while the spend is unknown. */
export function budgetRatio(budget: Pick<Budget, "amount_usd" | "state">): number | null {
  const spent = parseMoney(budget.state?.spent_usd);
  const amount = parseMoney(budget.amount_usd);
  return spent === null || amount === null || amount <= 0 ? null : spent / amount;
}

/** The enabled project-wide budget the Spend card measures against, if any. */
export function projectBudget(budgets: readonly Budget[]): Budget | null {
  return budgets.find((budget) => budget.enabled && budget.scope === "project") ?? null;
}

interface AttentionInput {
  insights: readonly Insight[];
  rules: readonly AlertRule[];
  budgets: readonly Budget[];
  now: Date;
}

/**
 * What needs a look (spec §4 "To do"): the three most severe open findings, then firing alert
 * rules, then enabled budgets at 80 % or more of their amount; at most six.
 */
export function attentionItems({ insights, rules, budgets, now }: AttentionInput): AttentionItem[] {
  const findings: AttentionItem[] = [...insights]
    .sort((a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity])
    .slice(0, MAX_FINDINGS)
    .map((insight) => ({
      kind: "finding",
      id: insight.id,
      title: insight.title,
      meta: `Seen ${formatRelativeTime(insight.last_seen_at, now) ?? ""}`,
      severity: insight.severity,
      to: "/$orgId/$projectId/doctor/$insightId",
      params: { insightId: insight.id },
    }));
  const alerts: AttentionItem[] = rules
    .filter((rule) => rule.enabled && isFiring(rule))
    .map((rule) => {
      const since = formatRelativeTime(rule.state?.since, now);
      return {
        kind: "alert",
        id: rule.id,
        title: rule.name,
        meta: since ? `Firing since ${since}` : "Firing",
        severity: "critical",
        to: "/$orgId/$projectId/alerts/$ruleId",
        params: { ruleId: rule.id },
      };
    });
  const spent: AttentionItem[] = budgets.flatMap((budget) => {
    const ratio = budgetRatio(budget);
    if (!budget.enabled || ratio === null || ratio < BUDGET_WARNING_RATIO) {
      return [];
    }
    return [
      {
        kind: "budget",
        id: budget.id,
        title: budget.name,
        // Floor, so 99.6 % never reads as a spent budget.
        meta: `${String(Math.floor(ratio * 100))}% of ${formatCost(budget.amount_usd) ?? ""} · ${budget.period}`,
        severity: ratio >= 1 ? "critical" : "warning",
        to: "/$orgId/$projectId/budgets",
        params: {},
      },
    ];
  });
  return [...findings, ...alerts, ...spent].slice(0, MAX_ITEMS);
}
