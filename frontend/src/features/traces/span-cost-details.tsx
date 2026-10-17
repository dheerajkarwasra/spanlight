import { ValueOrUnknown } from "@/components/unknown-value";
import type { CostExplanation, OriginalUsage, Span } from "@/lib/api";
import { formatInteger } from "@/lib/format";

import { reasonLabel, tierLabel } from "./cost-explanation";
import { MetaField } from "./meta-field";

const GRID = "grid grid-cols-1 gap-3 sm:grid-cols-4";

/** Why the price row cannot be shown, by status; the version is the span's `pricing_version`. */
function missingRowReason(status: CostExplanation["status"], version: string | null): string {
  switch (status) {
    case "no_price":
      return "No price row matched";
    case "override_deleted":
      return "The override that priced this span was deleted";
    case "price_version_missing":
      return `The price row that priced this span${version === null ? "" : ` (version ${version})`} is no longer in the price table; the stored figure is shown as it was priced.`;
    default:
      return "No price row produced this cost";
  }
}

function priceRowText(price: CostExplanation["price"]): string | null {
  if (price === null) {
    return null;
  }
  if (price.min_prompt_tokens === 0) {
    return price.model_pattern;
  }
  return `${price.model_pattern} · prompts ≥ ${formatInteger(price.min_prompt_tokens) ?? price.min_prompt_tokens}`;
}

/** Figma "Price footer": the row, version and tier that produced the stored cost. */
export function PriceFooter({ explanation, span }: { explanation: CostExplanation; span: Span }) {
  const { price, status } = explanation;
  const rowReason = missingRowReason(status, span.pricing_version);
  const source = status === "override_deleted" ? "override (deleted)" : (price?.source ?? null);
  return (
    <div role="group" aria-label="Pricing">
      <dl className={GRID}>
        <MetaField layout="stacked" label="Price row" mono={price !== null} wrap>
          <ValueOrUnknown value={priceRowText(price)} reason={rowReason} />
        </MetaField>
        <MetaField layout="stacked" label="Source">
          <ValueOrUnknown value={source} reason={rowReason} />
        </MetaField>
        <MetaField layout="stacked" label="Price version" mono={span.pricing_version !== null}>
          <ValueOrUnknown value={span.pricing_version} reason="No price was applied" />
        </MetaField>
        <MetaField layout="stacked" label="Service tier" wrap>
          <ValueOrUnknown
            value={tierLabel(
              explanation.service_tier,
              explanation.tier_multiplier,
              explanation.tier_note,
            )}
            reason="Not reported: priced at the standard rate"
          />
        </MetaField>
        <MetaField layout="stacked" label="Effective from" mono={price !== null}>
          <ValueOrUnknown value={price?.effective_from.slice(0, 10) ?? null} reason={rowReason} />
        </MetaField>
        <MetaField layout="stacked" label="Span id" mono>
          {span.span_id}
        </MetaField>
      </dl>
    </div>
  );
}

const NOT_REPORTED = "Not reported for this call";

function Tokens({ value }: { value: number | null }) {
  return <ValueOrUnknown value={formatInteger(value)} reason={NOT_REPORTED} className="tabular" />;
}

/** Figma "Known usage" of the no-usage state: what was counted, and why the rest was not. */
export function KnownUsage({ usage }: { usage: CostExplanation["usage"] }) {
  return (
    <dl className={GRID}>
      <MetaField layout="stacked" label="Input tokens">
        <Tokens value={usage.input_tokens} />
      </MetaField>
      <MetaField layout="stacked" label="Cached tokens">
        <Tokens value={usage.cached_tokens} />
      </MetaField>
      <MetaField layout="stacked" label="Output tokens">
        <Tokens value={usage.output_tokens} />
      </MetaField>
      <MetaField layout="stacked" label="Reason" wrap>
        <ValueOrUnknown
          value={reasonLabel(usage.unreported_reason)}
          reason="No reason was reported"
        />
      </MetaField>
    </dl>
  );
}

/** What the call that filled the gateway cache was billed for. */
export function OriginalUsageFields({ usage }: { usage: OriginalUsage }) {
  return (
    <dl className={GRID} aria-label="Usage of the call that filled the cache">
      <MetaField layout="stacked" label="Original input">
        <Tokens value={usage.input_tokens} />
      </MetaField>
      <MetaField layout="stacked" label="Original cached">
        <Tokens value={usage.cached_tokens} />
      </MetaField>
      <MetaField layout="stacked" label="Original output">
        <Tokens value={usage.output_tokens} />
      </MetaField>
    </dl>
  );
}
