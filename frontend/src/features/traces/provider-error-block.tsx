import { CircleAlert, RefreshCw } from "lucide-react";

import { SeriesKey } from "@/components/chart-parts";
import { UnknownValue } from "@/components/unknown-value";
import type { Span } from "@/lib/api";
import { errorClassColor, errorClassLabel } from "@/lib/error-class";
import { rateLimitKindLabel } from "@/lib/rate-limit-kind";

import { AttemptLogTable } from "./attempt-log-table";
import { MetaField } from "./meta-field";
import {
  attemptRows,
  attemptTotal,
  formatSeconds,
  gatewayErrorCodeOf,
  gatewayStatusOf,
  isGatewayError,
  requestIdOf,
  retryAfterSeconds,
  snapshotRows,
} from "./provider-error-format";
import { RatelimitSnapshot } from "./ratelimit-snapshot";

const REASONS = {
  type: "the error type was not sent with this span",
  code: "the error code was not sent with this span",
  retryAfter: "no retry-after was sent for this call",
  requestId: "no provider request id was captured for this span",
} as const;

/** Long ids and codes wrap at any character instead of being cut, so the full value is readable. */
const WRAP_ANYWHERE = "[overflow-wrap:anywhere]";

/**
 * Figma "Span detail/Provider error block": what the provider said about a failed LLM call (type,
 * code, the kind of limit, retry-after, request id, the gateway's answer), its rate-limit headers
 * and the gateway's attempt log. A failure the gateway made itself (its budget, its key limit, its
 * own fault) is titled "Gateway error" and shows the gateway's code, so it is never charged to the
 * provider. A gateway call that succeeded after a retry or a fallback shows the attempt log alone.
 * Render only when `showsProviderErrorBlock(span)`.
 */
export function ProviderErrorBlock({ span }: { span: Span }) {
  const failed = span.status === "error";
  const rows = attemptRows(span.attributes);
  const title = !failed
    ? "Gateway attempts"
    : isGatewayError(span)
      ? "Gateway error"
      : "Provider error";

  return (
    <section
      aria-label={title}
      className="flex min-w-0 flex-col gap-3 rounded-tile bg-surface-muted p-4"
    >
      <header className="flex min-w-0 flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
          {failed ? (
            <CircleAlert aria-hidden className="size-4 text-danger-text" />
          ) : (
            <RefreshCw aria-hidden className="size-4 text-muted-foreground" />
          )}
          {title}
        </h3>
        {failed && span.error_class !== null ? (
          <span className="flex items-center gap-1.5 text-xs font-medium text-foreground">
            <SeriesKey color={errorClassColor(span.error_class)} shape="bar" />
            {errorClassLabel(span.error_class)}
          </span>
        ) : null}
      </header>
      {failed ? (
        <>
          <ProviderErrorFields span={span} />
          <RatelimitSnapshot rows={snapshotRows(span.attributes)} />
        </>
      ) : null}
      {rows.length > 0 ? (
        <AttemptLogTable rows={rows} total={attemptTotal(span.attributes, rows)} />
      ) : null}
    </section>
  );
}

function ProviderErrorFields({ span }: { span: Span }) {
  const retryAfter = retryAfterSeconds(span.attributes);
  const requestId = requestIdOf(span.attributes);
  const gatewayStatus = gatewayStatusOf(span.attributes);
  const gatewayCode = gatewayErrorCodeOf(span.attributes);

  return (
    <dl className="flex flex-col">
      {gatewayCode !== null ? (
        <MetaField layout="inline" label="Gateway code" mono wrap>
          <span className={WRAP_ANYWHERE}>{gatewayCode}</span>
        </MetaField>
      ) : null}
      <MetaField layout="inline" label="Type" mono={span.provider_error_type !== null} wrap>
        {span.provider_error_type !== null ? (
          <span className={WRAP_ANYWHERE}>{span.provider_error_type}</span>
        ) : (
          <UnknownValue reason={REASONS.type} />
        )}
      </MetaField>
      <MetaField layout="inline" label="Code" mono={span.provider_error_code !== null} wrap>
        {span.provider_error_code !== null ? (
          <span className={WRAP_ANYWHERE}>{span.provider_error_code}</span>
        ) : (
          <UnknownValue reason={REASONS.code} />
        )}
      </MetaField>
      {span.rate_limit_kind !== null ? (
        <MetaField layout="inline" label="Kind">
          {rateLimitKindLabel(span.rate_limit_kind)} ·{" "}
          <span className="font-mono text-label">{span.rate_limit_kind}</span>
        </MetaField>
      ) : null}
      <MetaField layout="inline" label="Retry-after">
        {retryAfter !== null ? (
          <span className="tabular">{formatSeconds(retryAfter)}</span>
        ) : (
          <UnknownValue reason={REASONS.retryAfter} />
        )}
      </MetaField>
      {requestId !== null ? (
        <MetaField
          layout="inline"
          label="Request id"
          mono
          wrap
          copyValue={requestId}
          copyLabel="Copy request id"
        >
          <span className={WRAP_ANYWHERE}>{requestId}</span>
        </MetaField>
      ) : (
        <MetaField layout="inline" label="Request id">
          <UnknownValue reason={REASONS.requestId} />
        </MetaField>
      )}
      {gatewayStatus !== null ? (
        <MetaField layout="inline" label="Gateway answered">
          <span className="tabular">{gatewayStatus}</span>
        </MetaField>
      ) : null}
    </dl>
  );
}
