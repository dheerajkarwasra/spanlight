import { CopyButton } from "@/components/copy-button";
import { JsonViewer } from "@/components/json-viewer";
import { Card } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ScoresPanel, SpanScores } from "@/features/scores";
import type { Span } from "@/lib/api";
import { cn } from "@/lib/utils";

import { payloadToClipboardText } from "./chat-messages";
import { INSPECTOR_TABS, resolveTab, tabApplies, type InspectorTab } from "./inspector-tabs";
import { PayloadView } from "./payload-view";
import { ProviderErrorBlock } from "./provider-error-block";
import { showsProviderErrorBlock } from "./provider-error-format";
import { SpanCostCard } from "./span-cost-card";
import { SpanDetailHeader } from "./span-detail-header";
import { SpanMetadata } from "./span-metadata";
import { SpanReplaysTab } from "./span-replays-tab";
import { llmInputCandidates, llmOutputCandidates, type SpanNode } from "./span-tree";
import { SpanUsageCard } from "./span-usage-card";

const TAB_LABELS: Record<InspectorTab, string> = {
  messages: "Messages",
  cost: "Cost",
  scores: "Scores",
  attributes: "Attributes",
  raw: "Raw JSON",
  replays: "Replays",
};

/** One row of pills that scrolls sideways when the inspector is narrow (spec §6, D3). */
const TAB_LIST = "flex w-full justify-start";
const TAB_TRIGGER = "h-[34px] px-3.5 @2xl:flex-1";

function isInspectorTab(value: string): value is InspectorTab {
  return (INSPECTOR_TABS as readonly string[]).includes(value);
}

interface SpanDetailPanelProps {
  span: Span;
  /** The requested tab as the URL has it (any string); one that doesn't apply shows Messages. */
  tab: string | undefined;
  onTabChange: (tab: InspectorTab) => void;
  inTrace: SpanInTrace;
  className?: string;
}

/** Where the span sits in its trace. */
export interface SpanInTrace {
  /** The trace the span belongs to, for its Cost, Scores and Replays tabs. */
  traceId: string;
  /** The span's children; a span without I/O shows its LLM descendants' instead. */
  childNodes: readonly SpanNode[];
  onSelectSpan: (spanId: string) => void;
}

/**
 * Figma "Span detail" (UX Phase 3 inspector): the span header, then Messages · Cost · Scores ·
 * Attributes · Raw JSON · Replays. Cost shows for spans with a model call, Replays for LLM spans.
 */
export function SpanDetailPanel({
  span,
  tab,
  onTabChange,
  inTrace,
  className,
}: SpanDetailPanelProps) {
  const { traceId, childNodes, onSelectSpan } = inTrace;
  const tabs = INSPECTOR_TABS.filter((candidate) => tabApplies(candidate, span));
  const shownTab = resolveTab(tab, span);
  return (
    <Card className={cn("flex min-w-0 flex-col gap-4 p-5 sm:p-6", className)}>
      <section aria-label={`Span ${span.name}`} className="flex min-w-0 flex-col gap-4">
        <SpanDetailHeader span={span} />
        {showsProviderErrorBlock(span) ? <ProviderErrorBlock span={span} /> : null}
        <Tabs
          value={shownTab}
          onValueChange={(next) => {
            if (isInspectorTab(next)) {
              onTabChange(next);
            }
          }}
          className="@container flex min-w-0 flex-col"
        >
          <TabsList aria-label="Span details" className={TAB_LIST}>
            {tabs.map((value) => (
              <TabsTrigger
                key={value}
                value={value}
                className={TAB_TRIGGER}
                onClick={() => {
                  // Choosing the tab already shown as a fallback (`tab=cost` on a chain span,
                  // an old `io` link) fires no change; record it so the URL says what shows.
                  if (value === shownTab && tab !== value) {
                    onTabChange(value);
                  }
                }}
              >
                {TAB_LABELS[value]}
              </TabsTrigger>
            ))}
          </TabsList>
          <TabsContent value="messages" className="flex flex-col gap-2.5">
            <PayloadView
              label="Input"
              value={span.input}
              spanTruncated={span.truncated}
              fallback={{ candidates: llmInputCandidates(childNodes), traceId, onSelectSpan }}
            />
            <PayloadView
              label="Output"
              value={span.output}
              spanTruncated={span.truncated}
              fallback={{ candidates: llmOutputCandidates(childNodes), traceId, onSelectSpan }}
            />
          </TabsContent>
          {tabs.includes("cost") ? (
            <TabsContent value="cost" className="flex flex-col gap-4">
              <SpanUsageCard span={span} />
              <SpanCostCard span={span} traceId={traceId} defaultExpanded />
            </TabsContent>
          ) : null}
          <TabsContent value="scores" className="flex flex-col gap-4">
            <ScoresPanel traceId={traceId} />
            <SpanScores traceId={traceId} spanId={span.span_id} />
          </TabsContent>
          <TabsContent value="attributes">
            <SpanMetadata span={span} />
          </TabsContent>
          <TabsContent value="raw" className="flex flex-col gap-2">
            <div className="flex justify-end">
              <CopyButton value={payloadToClipboardText(span)} label="Copy span JSON" showLabel />
            </div>
            <JsonViewer
              value={span}
              defaultExpandDepth={1}
              prose={false}
              className="rounded-input px-3 py-3.5"
            />
          </TabsContent>
          {tabs.includes("replays") ? (
            <TabsContent value="replays">
              <SpanReplaysTab traceId={traceId} spanId={span.span_id} />
            </TabsContent>
          ) : null}
        </Tabs>
      </section>
    </Card>
  );
}
