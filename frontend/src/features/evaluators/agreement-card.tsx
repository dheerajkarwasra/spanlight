import { Link } from "@tanstack/react-router";
import { useId, type ReactNode } from "react";

import { bandText, formatKappa } from "@/components/agreement-format";
import { Callout } from "@/components/callout";
import { ErrorState } from "@/components/error-state";
import { SectionCard } from "@/components/section-card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { UnknownValue } from "@/components/unknown-value";
import { useProjectParams } from "@/features/shell";
import type { Agreement } from "@/lib/api";
import { formatInteger } from "@/lib/format";
import { cn } from "@/lib/utils";

import { useEvaluatorAgreement } from "./evaluator-queries";

const UNDEFINED_COPY = "All pairs are in one class, so κ can't be computed.";

const KAPPA_KINDS = { quadratic_weighted: "Quadratic-weighted κ", cohen: "Cohen’s κ" } as const;

/**
 * Figma "Agreement with reviewers" (755:1841, states 757:216): how a judge's newest scores agree
 * with the newest review scores on the same traces. Judges only.
 */
export function AgreementCard({ evaluatorId }: { evaluatorId: string }) {
  const query = useEvaluatorAgreement(evaluatorId);
  const agreement = query.data;
  const paired =
    agreement?.status === "ok" || agreement?.status === "undefined"
      ? `${formatInteger(agreement.pairs) ?? ""} paired traces`
      : null;
  return (
    <SectionCard
      title="Agreement with reviewers"
      description="This judge's newest score vs. the newest review score on the same trace."
      actions={
        paired ? <span className="text-sm font-medium text-muted-foreground">{paired}</span> : null
      }
      className="gap-4"
    >
      {query.isPending ? (
        <div role="status" aria-label="Loading agreement">
          <Skeleton className="h-[200px] rounded-tile" />
        </div>
      ) : agreement === undefined ? (
        <ErrorState
          compact
          error={query.error}
          title="Couldn't load the agreement"
          onRetry={() => {
            void query.refetch();
          }}
        />
      ) : (
        <AgreementBody agreement={agreement} />
      )}
    </SectionCard>
  );
}

function AgreementBody({ agreement }: { agreement: Agreement }) {
  switch (agreement.status) {
    case "not_enough_pairs":
      return <NotEnoughPairs agreement={agreement} />;
    case "undefined":
      return (
        <div className="flex flex-col gap-2">
          <p className="text-[32px] leading-none font-extrabold text-foreground">
            κ <UnknownValue reason={UNDEFINED_COPY} />
          </p>
          <p className="text-sm font-medium text-muted-foreground">{UNDEFINED_COPY}</p>
        </div>
      );
    case "unsupported":
      return (
        <p className="text-sm font-medium text-muted-foreground">
          Agreement can't be computed for this score's type or scale.
        </p>
      );
    case "ok":
      return <Measured agreement={agreement} />;
  }
}

function NotEnoughPairs({ agreement: { pairs, min_pairs } }: { agreement: Agreement }) {
  const { orgId, projectId } = useProjectParams();
  return (
    <div className="flex flex-col items-start gap-3">
      <p className="text-sm font-semibold text-foreground">
        Not enough paired traces yet · {formatInteger(pairs)} of {formatInteger(min_pairs)}
      </p>
      {/* Decorative: the line above says the count. */}
      <div aria-hidden className="h-1.5 w-full overflow-hidden rounded-full bg-surface-muted">
        <div
          className="h-full rounded-full bg-accent"
          style={{ width: `${Math.min(100, (pairs / min_pairs) * 100)}%` }}
        />
      </div>
      <p className="text-sm font-medium text-muted-foreground">
        κ and F1 show once {formatInteger(min_pairs)} traces have both a review score and a score
        from this judge.
      </p>
      <Button asChild>
        <Link to="/$orgId/$projectId/queues" params={{ orgId, projectId }}>
          Review traces
        </Link>
      </Button>
    </div>
  );
}

function Measured({ agreement }: { agreement: Agreement }) {
  const { kappa, f1, pairs, min_pairs, capped, pass_threshold } = agreement;
  const pass =
    pass_threshold === null
      ? "Yes is the positive class"
      : `Pass = ${String(pass_threshold)} or more`;
  return (
    <div className="flex flex-col gap-4">
      <ul className="grid gap-3 sm:grid-cols-3">
        <Stat
          label={agreement.kappa_kind ? KAPPA_KINDS[agreement.kappa_kind] : "κ"}
          value={kappa === null ? <UnknownValue reason={UNDEFINED_COPY} /> : formatKappa(kappa)}
        >
          {kappa === null ? null : (
            <>
              <Badge variant="outline" className="w-fit">
                {bandText(kappa)}
              </Badge>
              <span className="hidden sm:block">
                Bands: 0.80+ strong · 0.60–0.79 moderate · below 0.60 weak
              </span>
            </>
          )}
        </Stat>
        <Stat
          label="F1"
          value={
            f1 === null ? (
              <UnknownValue reason="Unknown: F1 needs a pass from the reviewer or the judge." />
            ) : (
              f1.toFixed(2)
            )
          }
        >
          {pass}; reviewer is the reference
        </Stat>
        <Stat label="Paired traces" value={formatInteger(pairs)}>
          Needs {formatInteger(min_pairs)} or more
          {capped ? ` · newest ${formatInteger(pairs) ?? ""} used` : null}
        </Stat>
      </ul>
      {agreement.flagged ? (
        <Callout tone="warning">
          Weak agreement (κ below 0.60). Check the judge's prompt against reviewed traces before
          relying on its mean.
        </Callout>
      ) : null}
      {agreement.matrix.length > 0 ? (
        <Matrix
          labels={agreement.labels}
          matrix={agreement.matrix}
          numeric={agreement.data_type !== "boolean"}
        />
      ) : null}
    </div>
  );
}

/**
 * One figure: label, value and note stacked from `sm`; on phones the value sits on the right of
 * the label and note (Figma 375).
 */
function Stat({
  label,
  value,
  children,
}: {
  label: string;
  value: ReactNode;
  children: ReactNode;
}) {
  return (
    <li className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 rounded-tile bg-surface-muted p-4 sm:grid-cols-1 sm:content-start sm:items-start">
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      <span className="col-start-2 row-span-2 row-start-1 text-[32px] leading-none font-extrabold tracking-[-0.04em] text-foreground tabular sm:col-start-1 sm:row-span-1 sm:row-start-2 sm:py-1">
        {value}
      </span>
      <span className="flex flex-col gap-1.5 text-xs font-medium text-muted-foreground">
        {children}
      </span>
    </li>
  );
}

/** Rows are the reviewer, columns the judge; the diagonal (the matches) is tinted. */
function Matrix({
  labels,
  matrix,
  numeric,
}: {
  labels: readonly string[];
  matrix: readonly (readonly number[])[];
  numeric: boolean;
}) {
  const titleId = useId();
  const cell = "px-3 py-2.5 text-center font-mono text-xs tabular";
  return (
    <div className="flex flex-col gap-2">
      <h3 id={titleId} className="text-sm font-semibold text-foreground">
        Confusion matrix
      </h3>
      {/* Focusable so keyboard users can scroll it sideways on a phone. */}
      <div
        role="region"
        tabIndex={0}
        aria-labelledby={titleId}
        className="overflow-x-auto rounded-tile border border-border"
      >
        <table className="w-full min-w-[420px] border-collapse text-sm">
          <caption className="sr-only">
            Rows are the reviewer's score, columns the judge's score.
          </caption>
          <thead className="bg-surface-muted">
            <tr>
              <th
                scope="col"
                className="sticky left-0 bg-surface-muted px-3 py-2.5 text-left text-xs font-medium whitespace-nowrap text-muted-foreground"
              >
                Reviewer<span aria-hidden> ↓ ·</span> Judge<span aria-hidden> →</span>
              </th>
              {labels.map((label) => (
                <th
                  key={label}
                  scope="col"
                  className="px-3 py-2.5 text-center text-xs font-medium whitespace-nowrap text-muted-foreground"
                >
                  {label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {matrix.map((row, i) => (
              <tr key={labels[i] ?? i} className="border-t border-border">
                <th
                  scope="row"
                  className="sticky left-0 bg-surface px-3 py-2.5 text-left text-xs font-medium whitespace-nowrap text-foreground"
                >
                  {labels[i]}
                </th>
                {row.map((count, j) => (
                  <td
                    key={labels[j] ?? j}
                    className={cn(
                      cell,
                      i === j
                        ? "bg-surface-muted font-semibold text-foreground"
                        : "text-foreground",
                    )}
                  >
                    {formatInteger(count)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs font-medium text-muted-foreground">
        {numeric
          ? `Scores are put in ${String(labels.length)} equal bins over the config's range. `
          : "Yes/No score · Yes is the positive class. "}
        Highlighted cells are the matches.
        {numeric ? (
          <span className="sm:hidden"> Swipe the table sideways to see every bin.</span>
        ) : null}
      </p>
    </div>
  );
}
