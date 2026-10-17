import { SearchX } from "lucide-react";
import type { ReactNode } from "react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { Notice } from "@/components/notice";
import { ReadOnlyNote } from "@/components/read-only-note";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useTraceQuery } from "@/features/traces";
import { NOT_EXACT_REASON } from "@/features/traces/replay-button-state";
import { isApiError, type ReplayRefusal, type ReplaySource } from "@/lib/api";

import { ReplayBudgetMeter } from "./replay-budget-meter";
import { ReplayForm, type ReplayStart } from "./replay-form";
import { ReplayHeader } from "./replay-header";
import { providerKindOf, requestText } from "./replay-input";
import { useReplaySourceQuery } from "./replay-queries";
import { ReplayRequest } from "./replay-request";

/** Refusals the user can lift by editing the request; the others leave nothing to send. */
const EDITABLE_REFUSALS = new Set(["REPLAY_PAYLOAD_REDACTED", "REPLAY_PAYLOAD_TRUNCATED"]);

const VIEWER_NOTE =
  "Replaying needs the member role, because every replay is a real provider call that is paid for. You can still read the stored request and the budget.";

interface ReplayColumnProps {
  traceId: string;
  spanId: string;
  /** Open with the request in edit mode (`?edit`). */
  editing: boolean;
  /** The viewer holds `experiments:run`. */
  canRun: boolean;
}

function Refusal({ refusal, tone }: { refusal: ReplayRefusal; tone: "danger" | "warning" }) {
  return (
    <Notice tone={tone} role="status">
      <span className="font-mono text-2xs">{refusal.code}</span>
      <p className="mt-1">{refusal.detail}</p>
    </Notice>
  );
}

/** The column for a span the server can replay as stored (exact) or as its messages only. */
function Replayable({ source, props }: { source: ReplaySource; props: ReplayColumnProps }) {
  const trace = useTraceQuery(props.traceId);
  const body = source.body ?? {};
  const start: ReplayStart = {
    traceId: props.traceId,
    spanId: props.spanId,
    stored: body,
    text: requestText(body, undefined),
    kind: source.provider_kind ?? "openai",
    model: typeof body.model === "string" ? body.model : "",
  };
  const notExact =
    source.fidelity === "messages_only" ? (
      <Notice>{NOT_EXACT_REASON} Add what the call needs before replaying.</Notice>
    ) : null;
  if (!props.canRun) {
    return (
      <>
        {notExact}
        <ReplayRequest text={start.text} stored={start.stored} editing={false} />
        <ReplayBudgetMeter />
        <ReadOnlyNote>{VIEWER_NOTE}</ReadOnlyNote>
      </>
    );
  }
  return (
    <>
      {notExact}
      <ReplayForm start={start} editing={props.editing} trace={trace.data} />
    </>
  );
}

/** A redacted or cut request: only an edit of the span's stored input can be sent. */
function EditToReplay({ refusal, props }: { refusal: ReplayRefusal; props: ReplayColumnProps }) {
  const trace = useTraceQuery(props.traceId);
  const span = trace.data?.spans.find((candidate) => candidate.span_id === props.spanId);
  if (trace.isPending) {
    return <Skeleton className="h-80 w-full rounded-input" />;
  }
  if (span === undefined) {
    return (
      <ErrorState
        compact
        error={trace.error}
        title="Couldn't load the span's request"
        description={trace.isSuccess ? "The span is no longer stored in this trace." : undefined}
        onRetry={() => void trace.refetch()}
      />
    );
  }
  const start: ReplayStart = {
    traceId: props.traceId,
    spanId: props.spanId,
    stored: null,
    text: requestText(null, span.input),
    kind: providerKindOf(span.provider),
    model: span.model ?? "",
  };
  return (
    <>
      <Refusal refusal={refusal} tone="warning" />
      <ReplayForm start={start} editing trace={trace.data} />
    </>
  );
}

/**
 * Figma "Playground / Replay column": the replay of one stored LLM request, opened from the span
 * panel (`/playground?replay=<trace_id>:<span_id>`). Read-only for viewers.
 */
export function ReplayColumn(props: ReplayColumnProps) {
  const source = useReplaySourceQuery(props.traceId, props.spanId, { enabled: true });
  const trace = useTraceQuery(props.traceId);
  const span = trace.data?.spans.find((candidate) => candidate.span_id === props.spanId);
  let content: ReactNode;
  if (source.isPending) {
    content = (
      <div role="status" aria-label="Loading the stored request">
        <Skeleton className="h-80 w-full rounded-input" />
      </div>
    );
  } else if (source.isError) {
    content =
      isApiError(source.error) && source.error.status === 404 ? (
        <EmptyState
          icon={SearchX}
          title="Span not found"
          description="This span is not in this project, or retention removed it."
        />
      ) : (
        <ErrorState
          compact
          error={source.error}
          title="Couldn't read the stored request"
          onRetry={() => void source.refetch()}
        />
      );
  } else if (source.data.refusal === null) {
    content = <Replayable source={source.data} props={props} />;
  } else if (EDITABLE_REFUSALS.has(source.data.refusal.code) && props.canRun) {
    content = <EditToReplay refusal={source.data.refusal} props={props} />;
  } else {
    content = <Refusal refusal={source.data.refusal} tone="danger" />;
  }
  return (
    <Card
      role="group"
      aria-label="Replay"
      className="mx-auto flex w-full max-w-[720px] min-w-0 flex-col gap-4 rounded-card p-5 sm:p-6"
    >
      <ReplayHeader traceId={props.traceId} spanId={props.spanId} span={span} />
      {content}
    </Card>
  );
}
