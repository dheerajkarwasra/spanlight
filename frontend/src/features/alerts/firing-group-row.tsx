import { Link } from "@tanstack/react-router";
import { ChevronRight, TriangleAlert } from "lucide-react";
import { useId, useState } from "react";

import { UnknownValue } from "@/components/unknown-value";
import { useProjectParams } from "@/features/shell";
import type { AlertEvent, AlertRule } from "@/lib/api";
import { cn } from "@/lib/utils";

import { clockLabel, compactRelative } from "./alert-time";
import { eventTracesLink } from "./event-traces-link";
import type { FiringGroup } from "./firing-groups";
import { COMPARATOR_SYMBOLS, formatMetricValue } from "./metric-labels";
import { TimeText } from "./time-text";
import { resolvedAfter } from "./timeline-markers";

const LINK =
  "inline-flex min-h-6 items-center self-start rounded-xs font-semibold text-accent underline-offset-4 hover:underline";

interface FiringGroupRowProps {
  group: FiringGroup;
  /** Undefined for budget rules (not in the rules list) and while the rules load. */
  rule: AlertRule | undefined;
  now: Date;
}

/** One rule's firings: a disclosure row ("Fired 3 times · last 14 min ago") over its firings. */
export function FiringGroupRow({ group, rule, now }: FiringGroupRowProps) {
  const [expanded, setExpanded] = useState(false);
  const listId = useId();
  const last = compactRelative(group.lastAt, now);

  return (
    <li className="rounded-tile bg-surface-muted">
      <button
        type="button"
        aria-expanded={expanded}
        aria-controls={listId}
        onClick={() => {
          setExpanded((open) => !open);
        }}
        className="flex min-h-11 w-full items-center gap-3 rounded-tile px-3.5 py-3 text-left outline-none hover:bg-surface-hover focus-visible:outline-[1.5px] focus-visible:outline-offset-2 focus-visible:outline-ring"
      >
        <ChevronRight
          aria-hidden
          className={cn(
            "size-4 shrink-0 text-muted-foreground motion-safe:transition-transform",
            expanded && "rotate-90",
          )}
        />
        <span className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="truncate text-sm font-semibold text-foreground">{group.ruleName}</span>
          <span className="text-xs font-medium text-muted-foreground">
            Fired {group.countLabel} {group.countLabel === "1" ? "time" : "times"}
            {last === null ? null : ` · last ${last}`}
          </span>
        </span>
        {group.firing ? (
          <span className="inline-flex shrink-0 items-center gap-1 text-xs font-semibold text-danger-text">
            <TriangleAlert aria-hidden className="size-3.5" strokeWidth={2} />
            Firing
          </span>
        ) : null}
      </button>
      <ul id={listId} hidden={!expanded} aria-label={`Firings of ${group.ruleName}`}>
        {group.events.map((event) => (
          <FiringItem key={event.id} event={event} rule={rule} now={now} />
        ))}
      </ul>
    </li>
  );
}

function FiringItem({ event, rule, now }: { event: AlertEvent; rule?: AlertRule; now: Date }) {
  const params = useProjectParams();
  const clock = clockLabel(event.started_at, now);
  const link = eventTracesLink(event, rule, now);
  const value = rule ? formatMetricValue(rule.metric, event.value) : null;
  const line = rule ? formatMetricValue(rule.metric, event.threshold) : null;

  return (
    <li className="flex flex-col gap-1 border-t border-border px-3.5 py-2.5 text-xs font-medium text-muted-foreground">
      <p className="flex flex-wrap gap-x-2">
        <span className="text-foreground">
          <TimeText iso={event.started_at} text={clock} />
        </span>
        <span>{event.resolved_at === null ? "still firing" : resolvedAfter(event)}</span>
        {rule ? (
          <span className="font-mono">
            {value === null ? (
              <UnknownValue reason="The value at firing wasn't recorded." />
            ) : line === null ? (
              value
            ) : (
              `${value} ${COMPARATOR_SYMBOLS[rule.comparator]} ${line}`
            )}
          </span>
        ) : null}
      </p>
      {link?.to === "/$orgId/$projectId/budgets" ? (
        <Link to={link.to} params={params} className={LINK}>
          View budgets
        </Link>
      ) : null}
      {link?.to === "/$orgId/$projectId/traces" ? (
        <Link to={link.to} params={params} search={link.search} className={LINK}>
          View traces<span className="sr-only"> for the firing at {clock}</span>
        </Link>
      ) : null}
      {link?.unsupported.map((note) => (
        <p key={note}>{note}</p>
      ))}
    </li>
  );
}
