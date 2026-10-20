import { Link } from "@tanstack/react-router";
import { ChevronRight } from "lucide-react";

import { ColumnLabels } from "@/components/tile-list";
import { useProjectParams } from "@/features/shell";
import type { AlertChannel, AlertRule } from "@/lib/api";
import { cn } from "@/lib/utils";

import { ChannelChips } from "./channel-chips";
import { isStillFiring, rulePill } from "./rule-state";
import { AcknowledgedBadge, RuleStatePill, StillFiringBadge } from "./rule-state-pill";
import { ruleSummary } from "./rule-summary";

/** Column widths from Figma "Alerts/Rule row"; the columns appear once the card is wide enough. */
const COLUMNS = {
  rule: "min-w-0 flex-1",
  channels: "flex min-w-0 items-center @[52rem]:w-[270px] @[52rem]:shrink-0",
  badges: "hidden w-[120px] shrink-0 @[52rem]:flex",
  state: "hidden w-[170px] shrink-0 @[52rem]:flex",
  chevron: "hidden w-4 shrink-0 @[52rem]:block",
} as const;

export function RuleColumnLabels() {
  return (
    <ColumnLabels className="gap-4 pr-4 pl-5 @[52rem]:flex">
      <span className={COLUMNS.rule}>Rule</span>
      <span className="w-[270px] shrink-0">Channels</span>
      <span className="w-[120px] shrink-0" />
      <span className="w-[170px] shrink-0">State</span>
      <span className="w-4 shrink-0" />
    </ColumnLabels>
  );
}

interface RuleRowProps {
  rule: AlertRule;
  /** The org's channels by id; undefined while they load or when they failed. */
  channels: ReadonlyMap<string, AlertChannel> | undefined;
  /** Someone acknowledged the rule's open event. */
  acknowledged: boolean;
  now: Date;
}

/**
 * Figma "Alerts/Rule row": name and summary, channels, badges, state and a chevron. The whole
 * tile opens the rule; the link's accessible name is the rule's name. A firing rule is tinted;
 * a disabled one is dimmed.
 */
export function RuleRow({ rule, channels, acknowledged, now }: RuleRowProps) {
  const { orgId, projectId } = useProjectParams();
  const pill = rulePill(rule, now);
  const stillFiring = isStillFiring(rule, now);
  const badges = (
    <>
      {stillFiring ? <StillFiringBadge /> : null}
      {acknowledged ? <AcknowledgedBadge /> : null}
    </>
  );

  return (
    <li
      className={cn(
        "relative flex flex-col gap-2.5 rounded-tile p-4 transition-colors",
        "@[52rem]:min-h-[62px] @[52rem]:flex-row @[52rem]:items-center @[52rem]:gap-4 @[52rem]:py-3.5 @[52rem]:pr-4 @[52rem]:pl-5",
        pill.tone === "firing"
          ? "bg-danger-subtle hover:bg-danger-subtle/80"
          : "bg-surface-muted hover:bg-surface-hover",
      )}
    >
      {/* Narrow cards: state and badges lead the tile. */}
      <div className="flex flex-wrap items-center gap-1.5 @[52rem]:hidden">
        <RuleStatePill pill={pill} />
        {badges}
      </div>
      <div className={cn(COLUMNS.rule, "flex flex-col gap-[3px]")}>
        <Link
          to="/$orgId/$projectId/alerts/$ruleId"
          params={{ orgId, projectId, ruleId: rule.id }}
          className={cn(
            "truncate text-sm font-semibold outline-none after:absolute after:inset-0 after:rounded-tile focus-visible:after:outline-[1.5px] focus-visible:after:outline-offset-2 focus-visible:after:outline-ring",
            rule.enabled ? "text-foreground" : "text-muted-foreground",
          )}
        >
          {rule.name}
        </Link>
        <span className="text-xs leading-[1.4] font-medium text-muted-foreground @[52rem]:truncate">
          {ruleSummary(rule)}
        </span>
      </div>
      <div className={COLUMNS.channels}>
        <ChannelChips
          channelIds={rule.channel_ids}
          channels={channels}
          max={2}
          className="@[52rem]:flex-nowrap"
        />
      </div>
      <div className={cn(COLUMNS.badges, "items-center gap-1.5")}>{badges}</div>
      <div className={cn(COLUMNS.state, "items-center")}>
        <RuleStatePill pill={pill} />
      </div>
      <ChevronRight aria-hidden className={cn(COLUMNS.chevron, "size-4 text-foreground")} />
    </li>
  );
}
