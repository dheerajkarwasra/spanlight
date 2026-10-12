import { Search } from "lucide-react";
import { useState, type ReactElement } from "react";

import { DialogHeading } from "@/components/dialog-heading";
import { RelativeTime } from "@/components/relative-time";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogFooter,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import type { Evaluator } from "@/lib/api";
import { shortId } from "@/lib/format";
import { cn } from "@/lib/utils";

import { scoresAreBoolean } from "./evaluator-labels";
import { useTestEvaluator, useTestTracesQuery } from "./evaluator-queries";
import { TestFailure, TestResult } from "./test-result";

function TracePicker({ value, onChange }: { value: string; onChange: (traceId: string) => void }) {
  const [search, setSearch] = useState("");
  const traces = useTestTracesQuery(search);
  return (
    <fieldset className="flex flex-col gap-2">
      <legend className="mb-2 text-sm font-semibold text-foreground">Trace</legend>
      <div className="relative">
        <Search
          aria-hidden
          className="pointer-events-none absolute top-1/2 left-3.5 size-4 -translate-y-1/2 text-muted-foreground"
        />
        <Input
          type="search"
          aria-label="Search by trace id or name"
          placeholder="Search by trace id or name"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          className="pl-10"
        />
      </div>
      {traces.isPending ? (
        <Skeleton role="status" aria-label="Loading traces" className="h-[104px] rounded-tile" />
      ) : traces.data === undefined ? (
        <p role="alert" className="text-xs font-medium text-danger-text">
          Traces couldn't be loaded. Search again to retry.
        </p>
      ) : traces.data.length === 0 ? (
        <p className="text-xs font-medium text-muted-foreground">
          No traces in the last 30 days match.
        </p>
      ) : (
        <div className="flex flex-col gap-1.5">
          {traces.data.map((trace) => (
            <label
              key={trace.trace_id}
              className={cn(
                "flex cursor-pointer items-center gap-3 rounded-[14px] border px-3 py-2 text-sm",
                value === trace.trace_id
                  ? "border-accent bg-surface-selected"
                  : "border-transparent bg-surface-muted",
              )}
            >
              <input
                type="radio"
                name="test-trace"
                value={trace.trace_id}
                checked={value === trace.trace_id}
                onChange={() => onChange(trace.trace_id)}
                className="accent-[var(--accent)]"
              />
              <span className="font-mono text-xs text-accent">{shortId(trace.trace_id, 10)}</span>
              <span className="min-w-0 flex-1 truncate font-medium text-foreground">
                {trace.name ?? "Unnamed trace"}
              </span>
              <RelativeTime
                iso={trace.started_at}
                className="shrink-0 text-xs text-muted-foreground"
              />
            </label>
          ))}
        </div>
      )}
    </fieldset>
  );
}

interface TestRunDialogProps {
  evaluator: Evaluator;
  trigger: ReactElement;
}

/**
 * Figma "Test run dialog" (pick, result, failed): run the evaluator once on a recent trace. The
 * call is real and budgeted, and no score is written to the trace.
 */
export function TestRunDialog({ evaluator, trigger }: TestRunDialogProps) {
  const [open, setOpen] = useState(false);
  const [traceId, setTraceId] = useState("");
  const [tookMs, setTookMs] = useState<number | null>(null);
  const test = useTestEvaluator(evaluator.id);
  const model = evaluator.config.kind === "llm_judge" ? evaluator.config.model : null;

  function run() {
    const started = performance.now();
    setTookMs(null);
    test.mutate(traceId, { onSettled: () => setTookMs(performance.now() - started) });
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next || !test.isPending) {
          setOpen(next);
          if (!next) test.reset();
        }
      }}
    >
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      {open ? (
        <DialogContent
          hideClose
          className="max-h-[calc(100dvh-2rem)] max-w-[560px] gap-5 overflow-y-auto rounded-card p-6"
        >
          <DialogHeading
            title="Test run"
            description={`Run ${evaluator.name} once on a trace you pick. The budget applies. No score is written to the trace.`}
            showClose={test.isPending ? "disabled" : true}
          />
          <TracePicker
            value={traceId}
            onChange={(next) => {
              // The shown result belongs to the previous trace.
              setTraceId(next);
              test.reset();
            }}
          />
          {test.data ? (
            <TestResult
              result={test.data}
              judge={model !== null}
              booleanScores={scoresAreBoolean(evaluator)}
              model={model}
              tookMs={tookMs}
            />
          ) : null}
          {test.isError ? <TestFailure error={test.error} model={model} tookMs={tookMs} /> : null}
          <DialogFooter className="flex-row justify-end gap-2.5 max-sm:[&>*]:flex-1">
            <DialogClose asChild>
              <Button disabled={test.isPending}>Close</Button>
            </DialogClose>
            <Button
              variant="primary"
              disabled={traceId === ""}
              loading={test.isPending}
              onClick={run}
            >
              {test.data || test.isError ? "Run again" : "Run test"}
            </Button>
          </DialogFooter>
        </DialogContent>
      ) : null}
    </Dialog>
  );
}
