import { Ban, Play } from "lucide-react";
import { useId, useState } from "react";

import { ErrorState } from "@/components/error-state";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { isApiError, type ReplayInput, type ReplayProviderKind, type TraceDetail } from "@/lib/api";
import { parseMoney } from "@/lib/format";

import { replayCredentials } from "./credential-options";
import { useCredentialsQuery } from "./playground-queries";
import { ReplayFields, type ReplayDraft } from "./replay-fields";
import { buildReplayInput, requestPair } from "./replay-input";
import { useReplayBudgetQuery } from "./replay-queries";
import { ReplayRequest } from "./replay-request";
import { ReplayResultView } from "./replay-result";
import { ReplayBudgetMeter } from "./replay-budget-meter";
import { MAX_REPLAY_TOKENS, canRun, runDisabledReason, storedMaxTokens } from "./replay-state";
import { useReplayRun } from "./use-replay-run";

/** What the column opens with, resolved from the span's `replay-source` read and its trace. */
export interface ReplayStart {
  traceId: string;
  spanId: string;
  /** The stored request; null when the source was refused and only an edit can be sent. */
  stored: Record<string, unknown> | null;
  /** The request as editable text (the stored body, or the span's raw input). */
  text: string;
  kind: ReplayProviderKind;
  model: string;
}

interface ReplayFormProps {
  start: ReplayStart;
  /** Open with the request in edit mode (`?edit`, or a refused source). */
  editing: boolean;
  trace: TraceDetail | undefined;
}

function useReplayOff(): { budget: ReturnType<typeof useReplayBudgetQuery>; off: boolean } {
  const budget = useReplayBudgetQuery({ enabled: true });
  const notConfigured = isApiError(budget.error) && budget.error.code === "NOT_CONFIGURED";
  return { budget, off: notConfigured || parseMoney(budget.data?.monthly_budget_usd) === 0 };
}

/** The member's replay column: the request, the credential, model and cap, the budget, the run. */
export function ReplayForm({ start, editing, trace }: ReplayFormProps) {
  const reasonId = useId();
  const stored = storedMaxTokens(start.stored);
  const [edited, setEdited] = useState<string | null>(editing ? start.text : null);
  const [draft, setDraft] = useState<ReplayDraft>({
    credentialId: null,
    model: start.model,
    maxTokens: stored === null ? "" : String(Math.min(stored, MAX_REPLAY_TOKENS)),
  });
  const [sent, setSent] = useState<ReplayInput | null>(null);
  const credentialsQuery = useCredentialsQuery();
  const { budget, off } = useReplayOff();
  const { outcome, running, run } = useReplayRun();
  const credentials = replayCredentials(credentialsQuery.data ?? [], start.kind);
  const credentialId = draft.credentialId ?? credentials[0]?.id ?? null;
  const text = edited ?? start.text;
  const state = { ...draft, text, credentialId, running, budget: budget.data, replayOff: off };
  const reason = runDisabledReason(state);

  function submit() {
    if (!canRun(state) || credentialId === null) {
      return;
    }
    const input = buildReplayInput({
      ...start,
      ...draft,
      credentialId,
      text,
      initialModel: start.model,
    });
    setSent(input);
    void run(input);
  }

  return (
    <div className="flex flex-col gap-4">
      <ReplayRequest
        text={text}
        stored={start.stored}
        editing={edited !== null}
        onEdit={(next) => setEdited(next ?? (start.stored === null ? start.text : null))}
      />
      {credentialsQuery.isPending ? <Skeleton className="h-16 w-full rounded-input" /> : null}
      {credentialsQuery.isError ? (
        <ErrorState
          compact
          error={credentialsQuery.error}
          title="Couldn't load credentials"
          onRetry={() => void credentialsQuery.refetch()}
        />
      ) : null}
      {credentialsQuery.isSuccess ? (
        <ReplayFields
          draft={{ ...draft, credentialId }}
          onChange={(patch) => setDraft((current) => ({ ...current, ...patch }))}
          credentials={credentials}
          kind={start.kind}
          storedMaxTokens={stored}
        />
      ) : null}
      <ReplayBudgetMeter />
      <div className="flex flex-col gap-2">
        <Button
          variant="primary"
          className="w-full"
          loading={running}
          disabled={reason !== null}
          aria-describedby={reason === null ? undefined : reasonId}
          onClick={submit}
        >
          <Play aria-hidden />
          Run replay
        </Button>
        {reason === null ? null : (
          <p
            id={reasonId}
            className="flex items-start gap-1.5 text-xs leading-5 text-muted-foreground"
          >
            <Ban aria-hidden className="mt-0.5 size-3.5 shrink-0" />
            {reason}
          </p>
        )}
      </div>
      <ReplayResultView
        outcome={outcome}
        trace={trace}
        spanId={start.spanId}
        request={sent ? requestPair(sent, start.stored) : { before: null, after: null }}
      />
    </div>
  );
}
