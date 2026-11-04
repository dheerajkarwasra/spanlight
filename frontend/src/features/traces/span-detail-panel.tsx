import { useLayoutEffect, useRef, type RefObject } from "react";

import { CopyButton } from "@/components/copy-button";
import { JsonViewer } from "@/components/json-viewer";
import { Button } from "@/components/ui/button";
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
/** The house tab strip's 1 px border and 4 px padding (`border p-1`): the pill clears both. */
const STRIP_INSET = 5;

function isInspectorTab(value: string): value is InspectorTab {
  return (INSPECTOR_TABS as readonly string[]).includes(value);
}

interface SpanDetailPanelProps {
  span: Span;
  /** The requested tab as the URL has it (any string); one that doesn't apply shows Messages. */
  tab: string | undefined;
  onTabChange: (tab: InspectorTab) => void;
  inTrace: SpanInTrace;
  /** The span's stored content still being fetched (`span` is incomplete); absent when whole. */
  content?: SpanContentState;
  className?: string;
}

/**
 * The retrieval of a span the trace detail returned without its attributes or payloads. While
 * pending or failed the content tabs show it instead of the incomplete span (Figma S9 states).
 */
export type SpanContentState =
  { status: "pending" } | { status: "error"; error: unknown; onRetry: () => void };

/**
 * Focus ring for targets under `outline-none` (the house `TabsContent` sets it): without
 * `outline-solid`, Tailwind 4 resolves the ring's outline style to none.
 */
const FOCUS_RING =
  "focus-visible:outline-solid focus-visible:outline-[1.5px] focus-visible:outline-offset-2 focus-visible:outline-ring";

/** Retry and Copy JSON: the frames' 40 px desktop / 48 px phone secondary buttons. */
const CONTENT_BUTTON = "h-12 sm:h-10";

/**
 * Keeps keyboard focus in the tab panel while the loading line, the failure block and the loaded
 * content replace each other: a block that unmounts holding focus hands it to its tab panel, and
 * a block that mounts while the panel holds it takes it (`target`). Runs as a layout effect so
 * the hand-off happens before React removes the focused node.
 */
function useTabPanelFocus(
  block: RefObject<HTMLElement | null>,
  target: RefObject<HTMLElement | null>,
) {
  useLayoutEffect(() => {
    const node = block.current;
    const panel = node?.closest<HTMLElement>('[role="tabpanel"]');
    if (panel && document.activeElement === panel) {
      target.current?.focus();
    }
    return () => {
      if (node?.contains(document.activeElement)) {
        panel?.focus();
      }
    };
  }, [block, target]);
}

function LoadingLine() {
  const line = useRef<HTMLParagraphElement>(null);
  useTabPanelFocus(line, line);
  return (
    <p
      ref={line}
      role="status"
      tabIndex={-1}
      className={cn(
        "rounded-input bg-surface-muted px-4 py-3.5 text-sm font-semibold text-foreground outline-none",
        FOCUS_RING,
      )}
    >
      Loading span content…
    </p>
  );
}

function LoadFailed({ onRetry }: { onRetry: () => void }) {
  const block = useRef<HTMLDivElement>(null);
  const message = useRef<HTMLDivElement>(null);
  useTabPanelFocus(block, message);
  return (
    <div ref={block} role="alert" className="flex flex-col items-start gap-3">
      <div
        ref={message}
        tabIndex={-1}
        className={cn(
          "flex w-full flex-col gap-1 rounded-input bg-surface-muted px-4 py-3.5 outline-none",
          FOCUS_RING,
        )}
      >
        <h3 className="text-sm font-semibold text-foreground">Couldn’t load span content</h3>
        <p className="text-sm text-muted-foreground">
          Try again to view stored attributes and copy complete JSON.
        </p>
      </div>
      <Button variant="secondary" size="md" className={CONTENT_BUTTON} onClick={onRetry}>
        Retry
      </Button>
    </div>
  );
}

/** The Messages, Cost, Attributes and Raw JSON bodies while `content` is not yet whole. */
function SpanContentPanel({ state }: { state: SpanContentState }) {
  return state.status === "pending" ? <LoadingLine /> : <LoadFailed onRetry={state.onRetry} />;
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
  content,
  className,
}: SpanDetailPanelProps) {
  const { traceId, childNodes, onSelectSpan } = inTrace;
  const tabs = INSPECTOR_TABS.filter((candidate) => tabApplies(candidate, span));
  const shownTab = resolveTab(tab, span);
  const tabListRef = useRef<HTMLDivElement>(null);
  // A narrow strip opens scrolled to the start; bring the selected tab into view (frame 822:3319).
  useLayoutEffect(() => {
    const list = tabListRef.current;
    const active = list?.querySelector<HTMLElement>('[role="tab"][data-state="active"]');
    if (!list || !active) return;
    const strip = list.getBoundingClientRect();
    const pill = active.getBoundingClientRect();
    const left = strip.left + STRIP_INSET;
    const right = strip.right - STRIP_INSET;
    if (pill.left < left) list.scrollLeft -= left - pill.left;
    else if (pill.right > right) list.scrollLeft += pill.right - right;
  }, [shownTab, span.span_id]);
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
          <TabsList ref={tabListRef} aria-label="Span details" className={TAB_LIST}>
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
          <TabsContent value="messages" className={cn("flex flex-col gap-2.5", FOCUS_RING)}>
            {content ? (
              <SpanContentPanel state={content} />
            ) : (
              <>
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
              </>
            )}
          </TabsContent>
          {tabs.includes("cost") ? (
            <TabsContent value="cost" className={cn("flex flex-col gap-4", FOCUS_RING)}>
              {/* Usage reasons read the attributes: not shown until they are known. */}
              {content ? (
                <SpanContentPanel state={content} />
              ) : (
                <>
                  <SpanUsageCard span={span} />
                  <SpanCostCard span={span} traceId={traceId} defaultExpanded />
                </>
              )}
            </TabsContent>
          ) : null}
          <TabsContent value="scores" className="flex flex-col gap-4">
            <ScoresPanel traceId={traceId} />
            <SpanScores traceId={traceId} spanId={span.span_id} />
          </TabsContent>
          <TabsContent value="attributes" className={FOCUS_RING}>
            {content ? <SpanContentPanel state={content} /> : <SpanMetadata span={span} />}
          </TabsContent>
          <TabsContent value="raw" className={cn("flex flex-col gap-2", FOCUS_RING)}>
            {content ? (
              <>
                <div className="flex flex-col items-start gap-1.5">
                  <Button variant="secondary" size="md" className={CONTENT_BUTTON} disabled>
                    Copy JSON
                  </Button>
                  <p className="text-xs font-medium text-muted-foreground">
                    Available after span content loads.
                  </p>
                </div>
                <SpanContentPanel state={content} />
              </>
            ) : (
              <>
                <div className="flex flex-col items-start">
                  <CopyButton
                    value={payloadToClipboardText(span)}
                    label="Copy JSON"
                    showLabel
                    showIcon={false}
                    variant="secondary"
                    size="md"
                    className={CONTENT_BUTTON}
                  />
                </div>
                <JsonViewer
                  value={span}
                  defaultExpandDepth={1}
                  prose={false}
                  className="rounded-input px-3 py-3.5"
                />
              </>
            )}
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
