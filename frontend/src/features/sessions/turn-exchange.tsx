import { CircleAlert, RotateCw } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useSpansWithPayloads } from "@/features/traces";
import { errorMessage, type Span, type TraceSummary } from "@/lib/api";

import { FailedBubble, MessageBubble } from "./chat-bubble";
import { earliestFailedSpan, extractTurnConversation } from "./conversation";
import { SystemPromptBadge, SystemPromptBlock } from "./session-system-prompt";
import { systemPromptOf } from "./system-prompt";

interface ExchangeProps {
  trace: TraceSummary;
  spans: readonly Span[];
  name: string;
  /** The turn's steps pill, shown between the user's input and the answer. */
  pill: ReactNode;
  /** Whether this is turn 1 (it shows its prompt), and turn 1's prompt for later turns' badge. */
  systemPrompt: { first: boolean; firstPrompt: string | null };
}

/** A turn's bubbles; spans that came back over the turns page's payload budget load first. */
export function TurnExchange(props: ExchangeProps) {
  return props.spans.some((span) => span.payload_omitted) ? (
    <FetchedExchange {...props} />
  ) : (
    <LoadedExchange {...props} />
  );
}

/**
 * The bubbles of a turn whose spans came back over the turns page's payload budget: this turn
 * loads only those spans, one request each.
 */
function FetchedExchange(props: ExchangeProps) {
  const { trace, spans, name } = props;
  const loaded = useSpansWithPayloads(trace.trace_id, spans);
  const failed = trace.error_count > 0;

  if (loaded.error !== null) {
    return (
      <div className="flex flex-col gap-2.5">
        <div
          role="alert"
          className="flex flex-wrap items-center gap-2 rounded-tile bg-surface-muted px-4 py-3 text-xs font-medium text-muted-foreground"
        >
          <CircleAlert aria-hidden className="size-4 shrink-0 text-danger" />
          <span className="min-w-0 flex-1">
            Couldn&apos;t load this turn. {errorMessage(loaded.error)}
          </span>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              loaded.refetch();
            }}
          >
            <RotateCw aria-hidden />
            Try again
          </Button>
        </div>
        {failed ? <FailedBubble title={`${name} failed`} message={trace.error_message} /> : null}
      </div>
    );
  }

  if (loaded.isPending) {
    return (
      <div className="flex flex-col gap-2.5" aria-busy>
        <span className="sr-only" role="status">
          Loading conversation
        </span>
        <Skeleton className="ml-auto h-11 w-1/2 rounded-tile" />
        <Skeleton className="h-16 w-3/4 rounded-tile" />
      </div>
    );
  }

  return <LoadedExchange {...props} spans={loaded.spans} />;
}

function LoadedExchange({ trace, spans, name, pill, systemPrompt }: ExchangeProps) {
  const { userText, assistantText } = extractTurnConversation(spans);
  const prompt = systemPromptOf(spans);
  const failed = trace.error_count > 0;
  const failedSpan = failed ? earliestFailedSpan(spans) : null;
  const errorText = trace.error_message ?? failedSpan?.status_message ?? null;

  return (
    <div className="flex flex-col gap-2.5">
      {systemPrompt.first ? (
        prompt !== null ? (
          <SystemPromptBlock text={prompt} />
        ) : null
      ) : (
        <SystemPromptBadge first={systemPrompt.firstPrompt} current={prompt} />
      )}
      {userText !== null ? <MessageBubble role="user" text={userText} /> : null}
      {pill}
      {assistantText !== null ? <MessageBubble role="assistant" text={assistantText} /> : null}
      {failed ? (
        <FailedBubble title={`${failedSpan?.name ?? name} failed`} message={errorText} />
      ) : null}
      {userText === null && assistantText === null && !failed ? (
        <p className="rounded-tile border border-dashed border-border px-4 py-3 text-xs font-medium text-muted-foreground">
          No chat messages were captured for this turn. Payload capture may be off, or the trace has
          no LLM call.
        </p>
      ) : null}
    </div>
  );
}
