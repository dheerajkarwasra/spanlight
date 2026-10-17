import { Fragment } from "react";

import { UnknownValue } from "@/components/unknown-value";
import { formatDuration, formatInteger } from "@/lib/format";
import { cn } from "@/lib/utils";

import { formatSeconds, type AttemptRow } from "./provider-error-format";

const CUT_NOTE = "8 targets × 5 attempts allow up to 40; the log keeps 10.";
const BILLING_NOTE = "Whether a failed attempt was billed is not known and not guessed.";

const HEAD = "py-1.5 text-left text-overline text-muted-foreground uppercase";
const CELL = "py-1.5 align-top";

interface AttemptLogTableProps {
  rows: readonly AttemptRow[];
  /** The true number of attempts; more than `rows` when the log was cut to its first 9 and last. */
  total: number;
}

/** Figma "Span detail/Provider error block", attempts: the gateway's attempt log as a table. */
export function AttemptLogTable({ rows, total }: AttemptLogTableProps) {
  const cut = total > rows.length;
  return (
    <div className="flex flex-col gap-2">
      <h4 className="text-sm font-semibold text-foreground">
        Attempts · {formatInteger(total)}
        {cut ? " (first 9 and the last shown)" : null}
      </h4>
      <div className="rounded-input bg-surface px-3 py-1.5">
        <table aria-label="Attempts" className="w-full table-fixed text-xs tabular">
          <thead>
            <tr className="border-b border-border">
              <th scope="col" className={cn(HEAD, "w-7")}>
                #
              </th>
              <th scope="col" className={HEAD}>
                Credential
              </th>
              <th scope="col" className={cn(HEAD, "w-16 text-right")}>
                Result
              </th>
              <th scope="col" className={cn(HEAD, "w-16 text-right")}>
                Time
              </th>
              <th scope="col" className={cn(HEAD, "w-14 text-right")}>
                Retry-after
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => {
              const previous = rows[index - 1];
              const gap = previous !== undefined && row.number - previous.number > 1;
              return (
                <Fragment key={row.number}>
                  {gap ? (
                    <tr>
                      <td colSpan={5} className={cn(CELL, "text-muted-foreground")}>
                        … attempts {previous.number + 1} to {row.number - 1} not stored
                      </td>
                    </tr>
                  ) : null}
                  <AttemptLine row={row} />
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="text-xs font-medium text-muted-foreground">{cut ? CUT_NOTE : BILLING_NOTE}</p>
    </div>
  );
}

function AttemptLine({ row }: { row: AttemptRow }) {
  const duration = formatDuration(row.durationMs);
  const credential = row.name ?? (row.target !== null ? `target ${String(row.target + 1)}` : null);
  return (
    <tr>
      <td className={cn(CELL, "text-muted-foreground")}>{row.number}</td>
      <td className={cn(CELL, "truncate font-mono text-label text-foreground")}>
        {credential !== null ? (
          <span title={credential}>{credential}</span>
        ) : (
          <UnknownValue reason="the attempt's credential is not known" />
        )}
      </td>
      <td className={cn(CELL, "truncate text-right text-foreground")}>
        {row.result ?? <UnknownValue reason="the attempt recorded no status or error" />}
      </td>
      <td className={cn(CELL, "text-right text-foreground")}>
        {duration ?? <UnknownValue reason="the attempt's duration was not recorded" />}
      </td>
      <td className={cn(CELL, "text-right text-foreground")}>
        {row.retryAfterS !== null ? (
          formatSeconds(row.retryAfterS)
        ) : (
          <UnknownValue reason="no retry-after came with this attempt" />
        )}
      </td>
    </tr>
  );
}
