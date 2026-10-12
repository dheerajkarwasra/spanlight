import type { ReactNode } from "react";

import { PromptVersionBadge } from "@/components/prompt-version-badge";
import { RelativeTime } from "@/components/relative-time";
import { SectionCard } from "@/components/section-card";
import { Skeleton } from "@/components/ui/skeleton";
import { UnknownValue } from "@/components/unknown-value";
import { useProjectParams } from "@/features/shell";
import {
  isApiError,
  type AlertChannel,
  type Credential,
  type Evaluator,
  type JudgeConfig,
} from "@/lib/api";

import { RULE_LABELS, samplingLabel } from "./evaluator-labels";
import { usePromptVersionByIdQuery } from "./evaluator-queries";

type Row = [label: string, value: ReactNode];

function JudgeTemplate({ config }: { config: JudgeConfig }) {
  const { orgId, projectId } = useProjectParams();
  const lookup = usePromptVersionByIdQuery(config.prompt_version_id);
  if (lookup.isPending) {
    return <Skeleton className="h-4 w-32" />;
  }
  if (lookup.data) {
    return (
      <PromptVersionBadge
        orgId={orgId}
        projectId={projectId}
        name={lookup.data.prompt_name}
        version={lookup.data.version}
      />
    );
  }
  return (
    <UnknownValue
      reason={
        isApiError(lookup.error) && lookup.error.isNotFound
          ? "This prompt version is no longer in the project."
          : "The prompt version couldn't be loaded."
      }
    />
  );
}

function kindRows(
  evaluator: Evaluator,
  credentials?: Credential[],
  channels?: AlertChannel[],
): Row[] {
  const config = evaluator.config;
  if (config.kind === "llm_judge") {
    const credential = credentials?.find((candidate) => candidate.id === config.credential_id);
    return [
      ["Template", <JudgeTemplate key="template" config={config} />],
      [
        "Credential",
        credential?.name ?? <UnknownValue reason="This credential couldn't be found." />,
      ],
      ["Model", <span className="font-mono text-xs">{config.model}</span>],
      ["Max tokens", String(config.max_tokens ?? 256)],
      [
        "Temperature",
        config.temperature === null || config.temperature === undefined
          ? "Provider default"
          : String(config.temperature),
      ],
    ];
  }
  if (config.kind === "webhook") {
    const channel = channels?.find((candidate) => candidate.id === config.channel_id);
    return [
      ["Channel", channel?.name ?? <UnknownValue reason="This channel couldn't be found." />],
      ["Payload version", "2026-10-01"],
    ];
  }
  const check = config.check;
  const rows: Row[] = [["Rule", RULE_LABELS[check.rule]]];
  if ("target" in check) {
    rows.push(["Checks", check.target === "input" ? "Input" : "Output"]);
  }
  if (check.rule === "regex") {
    rows.push([
      "Pattern",
      <span className="font-mono text-xs [overflow-wrap:anywhere]">{check.pattern}</span>,
    ]);
  }
  if (check.rule === "contains") {
    rows.push(["Phrase", check.needle]);
  }
  if (check.rule === "length") {
    rows.push(["Counts", check.unit === "words" ? "Words" : "Characters"]);
  }
  const onOff = (on: boolean) => (on ? "On" : "Off");
  if (check.rule === "regex") {
    rows.push(["Ignore case", onOff(check.ignore_case ?? false)]);
  }
  if (check.rule === "contains") {
    rows.push(["Case sensitive", onOff(check.case_sensitive ?? false)]);
  }
  if (check.rule === "exact_match") {
    rows.push(["Case sensitive", onOff(check.case_sensitive ?? true)]);
    rows.push(["Normalize whitespace", onOff(check.normalize_whitespace ?? true)]);
  }
  return rows;
}

function filterRows(evaluator: Evaluator): Row[] {
  const filters = evaluator.filters;
  const list = (values: string[] | undefined, empty: string) =>
    values && values.length > 0 ? values.join(", ") : empty;
  const rows: Row[] = [["Environments", list(filters.environments, "All")]];
  if (filters.models?.length) rows.push(["Models", list(filters.models, "All")]);
  if (filters.tags?.length) rows.push(["Tags", list(filters.tags, "Any")]);
  if (filters.trace_names?.length) rows.push(["Trace names", list(filters.trace_names, "All")]);
  if (filters.min_duration_ms !== null && filters.min_duration_ms !== undefined) {
    rows.push(["Min duration", `${filters.min_duration_ms} ms`]);
  }
  return rows;
}

interface ConfigCardProps {
  evaluator: Evaluator;
  credentials: Credential[] | undefined;
  channels: AlertChannel[] | undefined;
  /** The newest run's start; null with no runs, undefined while loading. */
  lastRunAt: string | null | undefined;
}

/** Figma "Configuration": what the evaluator calls or checks, sampling, filters, last run. */
export function ConfigCard({ evaluator, credentials, channels, lastRunAt }: ConfigCardProps) {
  const rows: Row[] = [
    ...kindRows(evaluator, credentials, channels),
    ["Sampling", samplingLabel(evaluator.sampling_rate)],
    ...filterRows(evaluator),
    [
      "Last run",
      lastRunAt === undefined ? (
        <Skeleton className="h-4 w-20" />
      ) : lastRunAt === null ? (
        "No runs yet"
      ) : (
        <RelativeTime iso={lastRunAt} />
      ),
    ],
  ];
  return (
    <SectionCard title="Configuration" className="gap-4">
      <dl className="grid grid-cols-[110px_minmax(0,1fr)] gap-x-4 gap-y-3 text-sm">
        {rows.map(([label, value]) => (
          <div key={label} className="contents">
            <dt className="text-xs font-medium text-muted-foreground">{label}</dt>
            <dd className="font-semibold text-foreground">{value}</dd>
          </div>
        ))}
      </dl>
    </SectionCard>
  );
}
