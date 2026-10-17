import { CircleAlert } from "lucide-react";

import { CopyButton } from "@/components/copy-button";
import { JsonViewer } from "@/components/json-viewer";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { SpanScores } from "@/features/scores";
import type { Span } from "@/lib/api";
import { formatDuration } from "@/lib/format";
import { cn } from "@/lib/utils";

import { payloadToClipboardText } from "./chat-messages";
import { PayloadView } from "./payload-view";
import { PromptVersionRef } from "./prompt-version-ref";
import { ProviderErrorBlock } from "./provider-error-block";
import { showsProviderErrorBlock } from "./provider-error-format";
import { ReplayButton } from "./replay-button";
import { SpanKindBadge } from "./span-kind";
import { SpanMetadata } from "./span-metadata";
import { SpanReplaysTab } from "./span-replays-tab";
import { SpanStatusBadge } from "./status";
import { TruncatedBadge } from "./truncated-badge";

const SPAN_DETAIL_TABS = ["io", "metadata", "raw", "scores", "replays"] as const;
export type SpanDetailTab = (typeof SPAN_DETAIL_TABS)[number];

function isSpanDetailTab(value: string): value is SpanDetailTab {
  return (SPAN_DETAIL_TABS as readonly string[]).includes(value);
}

/**
 * Figma "Traces/Pill tab": equal-width pills on a surface-muted track. Five labels need about
 * 570 px in one row, so a narrower card shows them as a two-column grid (Figma "Fixes
 * 2026-10-13", T7-06); the breakpoint is the card's own width, since the panel is narrow on
 * desktop too.
 */
const TAB_LIST = "grid w-full grid-cols-2 gap-1 rounded-tile border-0 @xl:flex @xl:rounded-full";
const TAB_TRIGGER = "h-[34px] min-w-0 px-3.5 @xl:flex-1";

interface SpanDetailPanelProps {
  span: Span;
  /** The open tab, kept by the page so it survives selecting another span. */
  tab: SpanDetailTab;
  onTabChange: (tab: SpanDetailTab) => void;
  /** The trace the span belongs to, for its Scores tab. */
  traceId: string;
  className?: string;
}

/**
 * Figma "Span detail": header with the Replay button, then Input / Output, Metadata, Raw JSON,
 * Scores and (LLM spans only) Replays tabs.
 */
export function SpanDetailPanel({
  span,
  tab,
  onTabChange,
  traceId,
  className,
}: SpanDetailPanelProps) {
  const isLlm = span.kind === "llm";
  // Only LLM spans have replays; a kept "replays" tab falls back to Input / Output elsewhere.
  const shownTab = tab === "replays" && !isLlm ? "io" : tab;
  return (
    <Card className={cn("flex min-w-0 flex-col gap-4 p-5 sm:p-6", className)}>
      <section aria-label={`Span ${span.name}`} className="flex min-w-0 flex-col gap-4">
        <SpanDetailHeader span={span} traceId={traceId} />
        {showsProviderErrorBlock(span) ? <ProviderErrorBlock span={span} /> : null}
        <Tabs
          value={shownTab}
          onValueChange={(next) => {
            if (isSpanDetailTab(next)) {
              onTabChange(next);
            }
          }}
          className="@container flex min-w-0 flex-col"
        >
          <TabsList aria-label="Span details" className={TAB_LIST}>
            <TabsTrigger value="io" className={TAB_TRIGGER}>
              Input / Output
            </TabsTrigger>
            <TabsTrigger value="metadata" className={TAB_TRIGGER}>
              Metadata
            </TabsTrigger>
            <TabsTrigger value="raw" className={TAB_TRIGGER}>
              Raw JSON
            </TabsTrigger>
            <TabsTrigger value="scores" className={TAB_TRIGGER}>
              Scores
            </TabsTrigger>
            {isLlm ? (
              <TabsTrigger value="replays" className={TAB_TRIGGER}>
                Replays
              </TabsTrigger>
            ) : null}
          </TabsList>
          <TabsContent value="io" className="flex flex-col gap-2.5">
            <PayloadView label="Input" value={span.input} spanTruncated={span.truncated} />
            <PayloadView label="Output" value={span.output} spanTruncated={span.truncated} />
          </TabsContent>
          <TabsContent value="metadata">
            <SpanMetadata span={span} />
          </TabsContent>
          <TabsContent value="raw" className="flex flex-col gap-2">
            <div className="flex justify-end">
              <CopyButton value={payloadToClipboardText(span)} label="Copy span JSON" showLabel />
            </div>
            <JsonViewer value={span} defaultExpandDepth={1} className="rounded-input px-3 py-3.5" />
          </TabsContent>
          <TabsContent value="scores">
            <SpanScores traceId={traceId} spanId={span.span_id} />
          </TabsContent>
          {isLlm ? (
            <TabsContent value="replays">
              <SpanReplaysTab traceId={traceId} spanId={span.span_id} />
            </TabsContent>
          ) : null}
        </Tabs>
      </section>
    </Card>
  );
}

function SpanDetailHeader({ span, traceId }: { span: Span; traceId: string }) {
  return (
    <header className="flex min-w-0 flex-col gap-2">
      <div className="flex min-w-0 items-center gap-2">
        <h2 title={span.name} className="min-w-0 flex-1 truncate text-card text-foreground">
          {span.name}
        </h2>
        <CopyButton value={span.span_id} label="Copy span ID" className="size-7 shrink-0" />
      </div>
      <div className="flex min-w-0 flex-wrap items-center gap-1.5 text-xs font-medium text-muted-foreground">
        <SpanKindBadge kind={span.kind} />
        <SpanStatusBadge status={span.status} />
        {span.truncated ? <TruncatedBadge /> : null}
        <span className="tabular">{formatDuration(span.duration_ms)}</span>
        {span.model ? (
          <>
            <span aria-hidden className="text-subtle-foreground">
              ·
            </span>
            <span title={span.model} className="max-w-56 truncate font-mono text-label font-normal">
              {span.model}
            </span>
          </>
        ) : null}
        {span.prompt_version_id ? <PromptVersionRef versionId={span.prompt_version_id} /> : null}
      </div>
      <ReplayButton traceId={traceId} span={span} />
      {span.status === "error" ? (
        <div
          role="note"
          className="mt-1 flex items-start gap-2 rounded-input bg-danger-subtle px-3 py-2.5 text-danger-text"
        >
          <CircleAlert aria-hidden className="mt-0.5 size-4 shrink-0" />
          <p className="min-w-0 font-mono text-xs leading-5 break-words whitespace-pre-wrap">
            {span.status_message ?? "The span failed without an error message."}
          </p>
        </div>
      ) : null}
    </header>
  );
}

export function SpanDetailSkeleton({ className }: { className?: string }) {
  return (
    <Card aria-hidden className={cn("flex flex-col gap-4 p-5 sm:p-6", className)}>
      <Skeleton className="h-5 w-48" />
      <div className="flex gap-1.5">
        <Skeleton className="h-6 w-11 rounded-full" />
        <Skeleton className="h-6 w-10 rounded-full" />
        <Skeleton className="h-6 w-28 rounded-full" />
      </div>
      <Skeleton className="h-[42px] w-full rounded-full" />
      <Skeleton className="h-20 w-[88%] self-center rounded-tile" />
      <Skeleton className="h-28 w-[85%] self-end rounded-tile" />
      <Skeleton className="h-36 w-[85%] rounded-tile" />
    </Card>
  );
}
