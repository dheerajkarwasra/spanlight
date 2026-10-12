import { getRouteApi, useNavigate } from "@tanstack/react-router";
import { useMemo, useState } from "react";

import { ErrorState } from "@/components/error-state";
import { MeshBackdrop } from "@/components/mesh-backdrop";
import { Skeleton } from "@/components/ui/skeleton";
import { useProjectParams } from "@/features/shell";

import { useQueueQuery, useScoreConfigsQuery } from "./annotation-queries";
import { progressTotal, queueConfigs } from "./queue-format";
import { activeChoiceScore, choicesOf } from "./score-draft";
import { ScoresPanel } from "./scores-panel";
import { ShortcutHelp } from "./shortcut-help";
import type { ShortcutAction } from "./shortcuts";
import { useItemDraft } from "./use-item-draft";
import { useWorkSession } from "./use-work-session";
import { useWorkShortcuts } from "./use-work-shortcuts";
import { WorkBar } from "./work-bar";
import { AllDoneCard, ReleasedCard } from "./work-end";
import { WorkItem } from "./work-item";
import { completionFor, visibleItem, type WorkState } from "./work-state";

const annotateRoute = getRouteApi("/_authed/$orgId/$projectId/annotate/$queueId");

/**
 * Figma "Annotate — work mode": a focus screen without the rail or top bar. The reviewer claims
 * one item at a time, scores it from the keyboard or the mouse, and moves on; leaving releases the
 * item they hold.
 */
export function AnnotatePage() {
  const { queueId } = annotateRoute.useParams();
  const { orgId, projectId } = useProjectParams();
  const navigate = useNavigate();
  const queue = useQueueQuery(queueId);
  const allConfigs = useScoreConfigsQuery();
  const configs = useMemo(
    () => (queue.data ? queueConfigs(queue.data, allConfigs.data) : []),
    [queue.data, allConfigs.data],
  );
  const session = useWorkSession(queueId);
  const { state } = session;
  const item = visibleItem(state);
  const draft = useItemDraft(item?.id ?? null, configs);
  const [helpOpen, setHelpOpen] = useState(false);

  function submit() {
    const scores = completionFor(state, configs, draft.values, draft.comment);
    if (scores !== null) {
      void session.complete(scores);
    }
  }

  function onAction(action: ShortcutAction) {
    switch (action.type) {
      case "toggle_help":
        setHelpOpen((open) => !open);
        return;
      case "escape":
        void navigate({
          to: "/$orgId/$projectId/queues/$queueId",
          params: { orgId, projectId, queueId },
        });
        return;
      case "submit":
        submit();
        return;
      case "skip":
        void session.skip();
        return;
      case "next":
        void session.claim();
        return;
      case "pick": {
        const target = activeChoiceScore(configs, draft.values, draft.focused);
        const option = target === null ? undefined : choicesOf(target)?.[action.index];
        if (target !== null && option !== undefined) {
          draft.setValue(target.name, option);
        }
      }
    }
  }
  useWorkShortcuts(helpOpen, onAction);

  const progress = queue.data?.progress ?? null;
  return (
    <div className="min-h-dvh bg-background lg:bg-canvas lg:p-3">
      <div className="relative flex min-h-dvh flex-col gap-6 overflow-clip bg-background px-4 pt-5 pb-10 lg:min-h-[calc(100dvh-1.5rem)] lg:gap-8 lg:rounded-card lg:px-10 lg:pt-7">
        <MeshBackdrop className="top-0 left-[420px] max-lg:-left-[200px]" />
        <div className="relative">
          <WorkBar
            queueId={queueId}
            queueName={queue.data?.name ?? null}
            subtitle={subtitleOf(state, progress)}
            progress={progress}
            onOpenHelp={() => setHelpOpen(true)}
          />
        </div>
        <main id="main" className="relative mx-auto flex w-full max-w-[1440px] flex-col">
          {queue.isError ? (
            <ErrorState error={queue.error} onRetry={() => void queue.refetch()} />
          ) : allConfigs.isError ? (
            <ErrorState
              error={allConfigs.error}
              title="Couldn't load the scores"
              onRetry={() => void allConfigs.refetch()}
            />
          ) : state.phase === "failed" ? (
            <ErrorState
              error={state.error}
              title="Couldn't claim an item"
              onRetry={() => void session.claim()}
            />
          ) : state.phase === "empty" ? (
            <AllDoneCard queueId={queueId} progress={progress} scoresPerItem={configs.length} />
          ) : state.phase === "released" ? (
            <ReleasedCard
              pending={progress?.pending ?? null}
              claiming={false}
              onClaim={() => void session.claim()}
            />
          ) : item === null || allConfigs.isPending ? (
            <WorkSkeleton />
          ) : (
            <div className="flex flex-col gap-5 lg:flex-row lg:items-start">
              <WorkItem item={item} />
              <ScoresPanel
                configs={configs}
                instructions={queue.data?.instructions ?? null}
                draft={draft}
                work={{
                  notice: state.notice,
                  busy: state.phase === "claiming" ? "claim" : state.saving,
                  onSubmit: submit,
                  onSkip: () => void session.skip(),
                  onRelease: () => void session.release(),
                }}
              />
            </div>
          )}
        </main>
      </div>
      <ShortcutHelp open={helpOpen} onOpenChange={setHelpOpen} />
    </div>
  );
}

function subtitleOf(
  state: WorkState,
  progress: { pending: number; done: number; skipped: number } | null,
) {
  if (state.phase === "empty") {
    return "Queue finished";
  }
  if (state.phase === "released") {
    return "No item claimed";
  }
  if (state.phase !== "working" || progress === null) {
    return "Claiming an item…";
  }
  const position = progress.done + progress.skipped + 1;
  return `Item ${String(position)} of ${String(progressTotal(progress))} · claimed by you`;
}

function WorkSkeleton() {
  return (
    <div role="status" aria-label="Claiming an item" className="flex flex-col gap-5 lg:flex-row">
      <Skeleton className="h-[480px] flex-1 rounded-card" />
      <Skeleton className="h-[480px] rounded-card lg:w-[410px]" />
    </div>
  );
}
