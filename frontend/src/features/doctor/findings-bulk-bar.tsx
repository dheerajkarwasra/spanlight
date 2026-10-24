import { useQueryClient } from "@tanstack/react-query";
import { BellOff, CircleCheck, X } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { useProjectParams } from "@/features/shell";
import { insightsApi, queryKeys, type Insight } from "@/lib/api";

import { availableActions } from "./action-rules";
import { MuteDialog } from "./mute-dialog";
import { runBulk } from "./run-bulk";

interface FindingsBulkBarProps {
  selected: Insight[];
  /** Leaves only these ids ticked (the failed ones, so a second click retries them). */
  keep: (ids: string[]) => void;
}

/**
 * Figma "Doctor list with bulk bar": a floating ink bar while rows are ticked, with Acknowledge
 * (when every ticked insight is open), Mute… (one dialog for all) and Clear. Each action loops
 * the per-insight route four at a time (spec ruling 16) and reports "N acknowledged · M failed".
 * Rendered only with `insights:manage`; the table (and so the selection) exists from `md` up.
 */
export function FindingsBulkBar({ selected, keep }: FindingsBulkBarProps) {
  const { projectId } = useProjectParams();
  const queryClient = useQueryClient();
  const [running, setRunning] = useState(false);
  // The ids the Mute dialog was opened for; the dialog stays mounted while the bar leaves.
  const [muteIds, setMuteIds] = useState<string[] | null>(null);
  const [result, setResult] = useState<{ line: string; ids: string; retry: () => void } | null>(
    null,
  );
  const ids = selected.map((insight) => insight.id);
  // The result belongs to the selection it left ticked; a later change of the ticks hides it.
  const shown = result?.ids === idsKey(ids) ? result : null;
  const barRef = useRef<HTMLDivElement>(null);

  /** True when every action succeeded (the selection is then empty and the bar gone). */
  async function run(
    ran: string,
    targets: string[],
    action: (id: string) => Promise<unknown>,
  ): Promise<boolean> {
    // The last result (and its retry button) stays while this runs, so focus has somewhere to stay.
    setRunning(true);
    const outcome = await runBulk(targets, action);
    setRunning(false);
    void queryClient.invalidateQueries({ queryKey: queryKeys.project(projectId).doctor });
    const failed = outcome.failed.map((failure) => failure.id);
    const line = `${String(outcome.ok.length)} ${ran}`;
    keep(failed);
    if (failed.length > 0) {
      setResult({
        line: `${line} · ${String(failed.length)} failed`,
        ids: idsKey(failed),
        retry: () => void runFromBar(ran, failed, action),
      });
      return false;
    }
    // Nothing left to retry: a later tick of the same ids must not bring the old line back.
    setResult(null);
    toast.success(line);
    return true;
  }

  /** From a bar button: focus would drop to the page when the bar leaves. */
  async function runFromBar(
    ran: string,
    targets: string[],
    action: (id: string) => Promise<unknown>,
  ) {
    if (await run(ran, targets, action)) focusTable();
  }

  return (
    <>
      {ids.length === 0 ? null : (
        <div
          ref={barRef}
          role="region"
          aria-label="Selected insights"
          className="fixed bottom-6 left-1/2 z-40 hidden -translate-x-1/2 items-center gap-3 rounded-full bg-hero-card py-2 pr-3 pl-5 text-hero-card-foreground shadow-lg md:flex dark:border dark:border-border"
        >
          <p className="text-sm font-semibold whitespace-nowrap" aria-live="polite">
            {ids.length} selected
            {shown ? <span className="font-medium"> · {shown.line}</span> : null}
          </p>
          {shown ? (
            <button
              type="button"
              aria-disabled={running || undefined}
              onClick={() => {
                if (!running) shown.retry();
              }}
              className="-ml-1.5 min-h-6 rounded-sm text-sm font-medium underline underline-offset-2 aria-disabled:opacity-60"
            >
              retry
            </button>
          ) : null}
          <span aria-hidden className="h-6 w-px bg-rail-tile" />
          {selected.every((insight) => availableActions(insight.status).acknowledge) ? (
            <Button
              variant="highlight"
              loading={running && muteIds === null}
              disabled={running}
              onClick={() => {
                void runFromBar("acknowledged", ids, (id) =>
                  insightsApi.acknowledge(projectId, id),
                );
              }}
            >
              <CircleCheck aria-hidden />
              Acknowledge
            </Button>
          ) : null}
          <Button
            disabled={running}
            onClick={() => {
              setMuteIds(ids);
            }}
          >
            <BellOff aria-hidden />
            Mute…
          </Button>
          <Button
            variant="ghost"
            disabled={running}
            className="text-hero-card-foreground hover:bg-rail-tile"
            onClick={() => {
              setResult(null);
              keep([]);
              focusTable();
            }}
          >
            <X aria-hidden />
            Clear
          </Button>
        </div>
      )}
      <MuteDialog
        open={muteIds !== null}
        count={muteIds?.length ?? 0}
        onOpenChange={(open) => {
          if (!open) setMuteIds(null);
        }}
        onSubmit={async (values) => {
          await run("muted", muteIds ?? [], (id) => insightsApi.mute(projectId, id, values));
        }}
        // The Mute… button is gone with the bar once every mute succeeded.
        onCloseAutoFocus={(event) => {
          // Read when focus moves, after the close rendered: the bar is gone if nothing failed.
          if (barRef.current) return;
          event.preventDefault();
          focusTable();
        }}
      />
    </>
  );
}

/** Ticked ids compare as a set: a refetch may reorder the rows. */
function idsKey(ids: string[]): string {
  return [...ids].sort().join();
}

/** The bar leaves with the selection, so focus goes to the table instead of the page. */
function focusTable() {
  const table = document.querySelector<HTMLElement>('[role="table"][aria-label="Insights"]');
  if (table) {
    table.tabIndex = -1;
    table.focus();
  }
}
