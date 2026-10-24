import { AlertTriangle, ArrowUpRight, Info } from "lucide-react";

import { UnknownValue } from "@/components/unknown-value";

import type { IncidentCitation } from "./incident-citation";

const TILE = "flex items-start gap-2.5 rounded-tile bg-surface-muted px-4 py-3 text-sm";

/**
 * Figma "Doctor/Insight detail/Incident line": the status-page incident the finding matched,
 * under the evidence metrics. Title and impact come from the provider's page and are rendered as
 * text; the title links to the page only when its URL is https.
 */
export function IncidentLine({ citation }: { citation: IncidentCitation }) {
  const title =
    citation.title === null ? (
      <UnknownValue reason="The status page gave no title for this incident" />
    ) : (
      citation.title
    );
  return (
    <p className={`${TILE} text-muted-foreground`}>
      <AlertTriangle aria-hidden className="mt-0.5 size-4 shrink-0 text-warning" />
      <span className="min-w-0 [overflow-wrap:anywhere]">
        Matches the provider’s status page incident:{" "}
        {citation.url === null || citation.title === null ? (
          <span className="font-semibold text-foreground">{title}</span>
        ) : (
          <a
            href={citation.url}
            target="_blank"
            rel="noopener noreferrer"
            className="rounded-sm font-semibold text-accent italic underline underline-offset-2"
          >
            {title}
            <span className="sr-only"> (opens in a new tab)</span>
          </a>
        )}
        {citation.impact === null ? null : (
          <span className="text-foreground"> ({citation.impact})</span>
        )}
        {citation.url === null || citation.title === null ? null : (
          <ArrowUpRight aria-hidden className="ml-0.5 inline size-3.5 text-foreground" />
        )}
      </span>
    </p>
  );
}

/** Why no incident could be matched when the server does not poll status pages. */
export function PollingOffLine() {
  return (
    <p className={`${TILE} text-muted-foreground`}>
      <Info aria-hidden className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
      <span className="min-w-0 [overflow-wrap:anywhere]">
        Provider status feeds are off on this server (
        <code className="font-mono text-xs">PROVIDER_STATUS_POLLING_ENABLED=false</code>), so no
        status-page incident could be matched.
      </span>
    </p>
  );
}
