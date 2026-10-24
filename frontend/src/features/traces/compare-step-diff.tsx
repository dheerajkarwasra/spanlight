import { useMemo, useRef, type Ref } from "react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import type { StepSpan } from "@/lib/api";

import { stepDiff, stepText, type DiffItem } from "./compare-diff";
import { CutNote, DiffColumn } from "./compare-diff-lines";
import { useStepSpanQuery } from "./trace-queries";
import { useSyncedScroll } from "./use-synced-scroll";

interface CompareStepDiffProps {
  traceIdA: string;
  traceIdB: string;
  spanA?: StepSpan | undefined;
  spanB?: StepSpan | undefined;
}

type Section = { label: string; items: DiffItem[]; cut: boolean };

/**
 * Figma "Traces — Compare / Step diff" (C2, C3): the step's input and output, A | B, as a line
 * diff. Removed lines are tinted in A, added ones in B, with a +/− mark and a spoken prefix.
 * Mounted only while open, so each side's payloads load then.
 */
export function CompareStepDiff({ traceIdA, traceIdB, spanA, spanB }: CompareStepDiffProps) {
  const a = useStepSpanQuery(traceIdA, spanA?.span_id);
  const b = useStepSpanQuery(traceIdB, spanB?.span_id);
  const refA = useRef<HTMLDivElement>(null);
  const refB = useRef<HTMLDivElement>(null);
  useSyncedScroll(refA, refB);

  const loading = (spanA !== undefined && a.isPending) || (spanB !== undefined && b.isPending);
  const failed = a.isError || b.isError;
  const ready = !loading && !failed;
  const sections: Section[] | null = useMemo(
    () =>
      ready
        ? (["input", "output"] as const).map((field) => ({
            label: field === "input" ? "Input" : "Output",
            ...stepDiff(stepText(a.data?.[field]), stepText(b.data?.[field])),
          }))
        : null,
    [ready, a.data, b.data],
  );
  const cut = sections?.some((section) => section.cut) ?? false;

  // The columns stay mounted in every state so the synced scroll keeps its two boxes.
  return (
    <div className="flex flex-col gap-2">
      {failed ? (
        <div
          role="alert"
          className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground"
        >
          Couldn&apos;t load this step&apos;s input and output.
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              void a.refetch();
              void b.refetch();
            }}
          >
            Retry
          </Button>
        </div>
      ) : null}
      {cut ? (
        <CutNote
          sides={[
            { label: "A", traceId: traceIdA, span: spanA },
            { label: "B", traceId: traceIdB, span: spanB },
          ]}
        />
      ) : null}
      <div className="grid gap-3 md:grid-cols-2">
        <Column
          ref={refA}
          title="A · Baseline"
          side="a"
          present={spanA !== undefined}
          sections={sections}
          failed={failed}
        />
        <Column
          ref={refB}
          title="B · Candidate"
          side="b"
          present={spanB !== undefined}
          sections={sections}
          failed={failed}
        />
      </div>
    </div>
  );
}

interface ColumnProps {
  ref: Ref<HTMLDivElement>;
  title: string;
  side: "a" | "b";
  present: boolean;
  /** Null while loading or after a failure. */
  sections: Section[] | null;
  failed: boolean;
}

/** One side of the diff. From `md` the two columns are capped scroll boxes that scroll together. */
function Column({ ref, title, side, present, sections, failed }: ColumnProps) {
  const hidden = side === "a" ? "added" : "removed";
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <p className="text-overline text-subtle-foreground uppercase">{title}</p>
      <div
        ref={ref}
        role="region"
        aria-label={`${title} input and output`}
        // A scroll box takes focus so the keyboard can scroll it.
        tabIndex={0}
        className="flex flex-col gap-3 rounded-tile bg-surface-muted p-3 md:max-h-96 md:overflow-y-auto"
      >
        {!present ? (
          <p className="text-sm text-muted-foreground">This step is not in this trace.</p>
        ) : failed ? (
          <p className="text-sm text-muted-foreground">Not loaded.</p>
        ) : sections === null ? (
          <div role="status" aria-label="Loading the step" className="flex flex-col gap-2">
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-4 w-1/2" />
          </div>
        ) : (
          sections.map((section) => (
            <div key={section.label} className="flex flex-col gap-1">
              <p className="text-xs font-medium text-muted-foreground">{section.label}</p>
              <DiffColumn
                label={`${title} ${section.label.toLowerCase()}`}
                items={section.items.filter((item) => item.kind !== hidden)}
              />
            </div>
          ))
        )}
      </div>
    </div>
  );
}
