import { Link } from "@tanstack/react-router";
import { TriangleAlert } from "lucide-react";

import { useProjectParams } from "@/features/shell";
import { useNow } from "@/lib/use-now";

import { untilLabel } from "./alert-time";
import { useOpenEventsQuery } from "./alerts-queries";
import { TimeText } from "./time-text";

const LINK =
  "rounded-xs font-semibold text-foreground underline underline-offset-4 hover:text-accent";

/**
 * Spec ruling 19: "2 rules firing · Error rate > 5 % since 14:02" while any rule fires, from the
 * polled open events (newest first, one per rule). Polite status; the danger colour stays on the
 * icon and the left edge, the text in `--text` for contrast.
 */
export function FiringBanner() {
  const params = useProjectParams();
  const openEvents = useOpenEventsQuery().data ?? [];
  const now = useNow();
  const first = openEvents[0];

  return (
    <div role="status">
      {first === undefined ? null : (
        <p className="flex items-start gap-2.5 rounded-tile border-l-4 border-danger bg-danger-subtle px-4 py-3 text-sm font-medium text-foreground">
          <TriangleAlert
            aria-hidden
            className="mt-0.5 size-4 shrink-0 text-danger"
            strokeWidth={2}
          />
          <span>
            {openEvents.length} {openEvents.length === 1 ? "rule" : "rules"} firing ·{" "}
            {first.rule_kind === "budget" ? (
              <Link to="/$orgId/$projectId/budgets" params={params} className={LINK}>
                {first.rule_name}
              </Link>
            ) : (
              <Link
                to="/$orgId/$projectId/alerts/$ruleId"
                params={{ ...params, ruleId: first.rule_id }}
                className={LINK}
              >
                {first.rule_name}
              </Link>
            )}{" "}
            since{" "}
            <TimeText iso={first.started_at} text={untilLabel(new Date(first.started_at), now)} />
          </span>
        </p>
      )}
    </div>
  );
}
