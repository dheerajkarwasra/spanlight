import type { ReactNode } from "react";

import { SectionCard } from "@/components/section-card";
import type { AlertChannel, AlertRule } from "@/lib/api";
import { errorClassLabel } from "@/lib/error-class";
import { rateLimitKindLabel } from "@/lib/rate-limit-kind";

import { ChannelChips } from "./channel-chips";
import { METRIC_LABELS } from "./metric-labels";
import { conditionLabel, directionLabel, minutesLong, RULE_KIND_LABELS } from "./rule-summary";

interface RuleSettingsCardProps {
  rule: AlertRule;
  /** The org's channels by id; undefined while they load or when they failed. */
  channels: ReadonlyMap<string, AlertChannel> | undefined;
}

/**
 * Figma "Rule": what the rule measures, as key/value rows, then its channels. Provider, model,
 * error class and rate-limit kind rows appear only when the rule filters on them; the environment
 * reads "Any" when it doesn't.
 */
export function RuleSettingsCard({ rule, channels }: RuleSettingsCardProps) {
  const { environment, provider, model, error_class, rate_limit_kind } = rule.filters;

  return (
    <SectionCard title="Rule" className="gap-1">
      <dl className="flex flex-col">
        <Row label="Type">{RULE_KIND_LABELS[rule.kind]}</Row>
        <Row label="Metric">{METRIC_LABELS[rule.metric].label}</Row>
        {rule.kind === "anomaly" ? (
          <Row label="Direction">{directionLabel(rule)}</Row>
        ) : (
          <Row label="Condition">{conditionLabel(rule)}</Row>
        )}
        <Row label="Window">{minutesLong(rule.window_minutes)}</Row>
        <Row label="Cooldown">{minutesLong(rule.cooldown_minutes)}</Row>
        <Row label="Environment">{environment ?? "Any"}</Row>
        {provider ? <Row label="Provider">{provider}</Row> : null}
        {model ? <Row label="Model">{model}</Row> : null}
        {error_class ? <Row label="Error class">{errorClassLabel(error_class)}</Row> : null}
        {rate_limit_kind ? (
          <Row label="Rate-limit kind">{rateLimitKindLabel(rate_limit_kind)}</Row>
        ) : null}
        <div className="flex flex-col gap-2 pt-3">
          <dt className="text-xs leading-[1.4] font-medium text-muted-foreground">Channels</dt>
          <dd>
            <ChannelChips channelIds={rule.channel_ids} channels={channels} />
          </dd>
        </div>
      </dl>
    </SectionCard>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-border py-[11px]">
      <dt className="text-xs leading-[1.4] font-medium text-muted-foreground">{label}</dt>
      <dd className="min-w-0 text-right text-sm font-medium [overflow-wrap:anywhere] text-foreground">
        {children}
      </dd>
    </div>
  );
}
