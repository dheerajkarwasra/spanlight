import { Link } from "@tanstack/react-router";
import { ArrowRight } from "lucide-react";

import { useProjectParams } from "@/features/shell";
import type { InsightDetail } from "@/lib/api";

// The catalogue's copy for `unpriced_spend` (backend `insights/catalogue.py`); the finding keeps
// its provider and model only in that text, "—" when the spans didn't report one.
const TITLE_SUFFIX = " has usage but no price";
const UNKNOWN = "—";

/** The finding's provider and model, read back from its title and summary. */
function unpricedTarget(insight: Pick<InsightDetail, "title" | "summary">): {
  provider?: string;
  model?: string;
} {
  if (!insight.title.endsWith(TITLE_SUFFIX)) {
    return {};
  }
  const model = insight.title.slice(0, -TITLE_SUFFIX.length);
  if (model === UNKNOWN || model === "") {
    return {};
  }
  const start = insight.summary.indexOf(" calls to ");
  const end = insight.summary.indexOf(`/${model} in 24 hours`);
  const provider = start >= 0 && end > start ? insight.summary.slice(start + 10, end) : UNKNOWN;
  return provider === UNKNOWN || provider === "" ? { model } : { provider, model };
}

/** Spec ruling 9: an `unpriced_spend` finding links to Cost › Pricing with the dialog prefilled. */
export function SetPriceLink({ insight }: { insight: InsightDetail }) {
  const { orgId, projectId } = useProjectParams();
  const { provider, model } = unpricedTarget(insight);
  return (
    <Link
      to="/$orgId/$projectId/pricing"
      params={{ orgId, projectId }}
      search={model === undefined ? {} : { provider, model }}
      className="inline-flex min-h-6 items-center gap-1.5 self-start rounded-sm text-sm font-semibold text-accent hover:underline max-md:min-h-11"
    >
      {model === undefined ? (
        "Set prices"
      ) : (
        <>
          Set a price for <span className="font-mono text-[13px]">{model}</span>
        </>
      )}
      <ArrowRight aria-hidden className="size-4" />
    </Link>
  );
}
