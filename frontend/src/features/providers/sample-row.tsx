import { Link } from "@tanstack/react-router";
import { ChevronRight } from "lucide-react";

import { SeriesKey } from "@/components/chart-parts";
import { UNKNOWN_DASH_CLASS } from "@/components/unknown-value";
import { useProjectParams } from "@/features/shell/project-context";
import type { ErrorSample } from "@/lib/api";
import { errorClassColor, errorClassLabel } from "@/lib/error-class";
import { pluralize } from "@/lib/format";
import { rateLimitKindLabel } from "@/lib/rate-limit-kind";

import { utcClock } from "./bucket-labels";
import { formatRetryAfter } from "./providers-format";
import { SAMPLE_COLUMNS } from "./sample-columns";

/**
 * A missing value inside the row link: the dash's look without its own focusable tooltip (a
 * control inside a link is invalid); the card's footer says what the dash means.
 */
function Dash() {
  return <span className={UNKNOWN_DASH_CLASS}>—</span>;
}

function Mono({ value, muted = false }: { value: string | null; muted?: boolean }) {
  if (value === null) {
    return <Dash />;
  }
  return (
    <span
      title={value}
      className={muted ? "block truncate text-muted-foreground" : "block truncate"}
    >
      {value}
    </span>
  );
}

/** One failed call; the whole tile opens its trace with the span selected. */
export function SampleRow({ sample }: { sample: ErrorSample }) {
  const { orgId, projectId } = useProjectParams();
  const time = utcClock(sample.started_at);
  const retryAfter = formatRetryAfter(sample.retry_after_s);
  const label = [
    time === null ? "Open trace: time unknown" : `Open trace: ${time} UTC`,
    sample.model ?? "model not reported",
    errorClassLabel(sample.error_class),
    sample.rate_limit_kind ? `kind ${rateLimitKindLabel(sample.rate_limit_kind)}` : null,
    sample.provider_error_type,
    sample.provider_error_code,
    retryAfter ? `retry-after ${retryAfter}` : null,
    sample.attempts === null ? null : pluralize(sample.attempts, "attempt"),
  ]
    .filter((part): part is string => part !== null)
    .join(", ");

  return (
    <li>
      <Link
        to="/$orgId/$projectId/traces/$traceId"
        params={{ orgId, projectId, traceId: sample.trace_id }}
        search={(prev) => ({ ...prev, span: sample.span_id })}
        aria-label={label}
        className="group flex flex-col gap-1 rounded-tile bg-surface-muted px-3.5 py-3 text-sm transition-colors hover:bg-surface-hover focus-visible:outline-offset-0 @[46rem]:flex-row @[46rem]:items-center @[46rem]:gap-3 @[46rem]:px-3 @[46rem]:py-2.5"
      >
        <span className="flex items-center gap-2 font-mono text-[13px] @[46rem]:contents">
          <span className={`text-muted-foreground ${SAMPLE_COLUMNS.time}`}>{time ?? <Dash />}</span>
          <span className={`min-w-0 flex-1 truncate @[46rem]:flex-none ${SAMPLE_COLUMNS.model}`}>
            {sample.model ?? <Dash />}
          </span>
          <ChevronRight
            aria-hidden
            className="size-4 shrink-0 text-muted-foreground @[46rem]:hidden"
          />
        </span>
        <span className={`flex items-center gap-1.5 font-medium ${SAMPLE_COLUMNS.errorClass}`}>
          <SeriesKey color={errorClassColor(sample.error_class)} shape="bar" />
          <span className="truncate">{errorClassLabel(sample.error_class)}</span>
        </span>
        <span className={`text-[13px] ${SAMPLE_COLUMNS.kind}`}>
          {sample.rate_limit_kind === null ? (
            <Dash />
          ) : (
            <>
              <span className="block truncate">{rateLimitKindLabel(sample.rate_limit_kind)}</span>
              <span className="block truncate font-mono text-muted-foreground">
                {sample.rate_limit_kind}
              </span>
            </>
          )}
        </span>
        <span className={`font-mono text-[13px] ${SAMPLE_COLUMNS.typeCode}`}>
          <Mono value={sample.provider_error_type} />
          {sample.provider_error_code ? <Mono value={sample.provider_error_code} muted /> : null}
        </span>
        <span className={`hidden tabular @[46rem]:block ${SAMPLE_COLUMNS.retryAfter}`}>
          {retryAfter ?? <Dash />}
        </span>
        <span className={`hidden tabular @[46rem]:block ${SAMPLE_COLUMNS.attempts}`}>
          {sample.attempts ?? <Dash />}
        </span>
        <span className="text-xs text-muted-foreground @[46rem]:hidden">
          retry-after {retryAfter ?? <Dash />} ·{" "}
          {sample.attempts === null ? <Dash /> : pluralize(sample.attempts, "attempt")}
        </span>
        <ChevronRight
          aria-hidden
          className="hidden size-4 shrink-0 text-muted-foreground @[46rem]:block"
        />
      </Link>
    </li>
  );
}
