import { statusPageUrl } from "@/lib/status-page-url";

/*
 * The provider-incident citation on a `provider_incident` finding: the detector adds
 * `incident_id`, `incident_title`, `incident_url` and `incident_impact` to the evidence metrics,
 * all four null when no status-page incident overlapped. The title and URL come from a provider's
 * status page, so they are rendered as text, and the URL becomes a link only when it passes the
 * shared status-page gate (`lib/status-page-url.ts`).
 */

type Metrics = Readonly<Record<string, string | number | null>>;

const INCIDENT_METRICS: ReadonlySet<string> = new Set([
  "incident_id",
  "incident_title",
  "incident_url",
  "incident_impact",
]);

/** The incident metrics are drawn as the citation line, never as metric tiles. */
export function isIncidentMetric(name: string): boolean {
  return INCIDENT_METRICS.has(name);
}

export interface IncidentCitation {
  /** Null when the status page gave none (shown as "—"). */
  title: string | null;
  /** Only a polled provider status page; anything else leaves the title unlinked. */
  url: string | null;
  impact: string | null;
}

function text(metrics: Metrics, name: string): string | null {
  const value = metrics[name];
  if (value === null || value === undefined) {
    return null;
  }
  const asText = String(value);
  return asText === "" ? null : asText;
}

/** The citation when an incident matched, else null. */
export function incidentCitation(metrics: Metrics): IncidentCitation | null {
  if (text(metrics, "incident_id") === null) {
    return null;
  }
  return {
    title: text(metrics, "incident_title"),
    url: statusPageUrl(text(metrics, "incident_url")),
    impact: text(metrics, "incident_impact"),
  };
}

/**
 * A `provider_incident` finding with no matched incident says the feeds are off when the server
 * does not poll them, so "no matching incident" is not read as "all clear".
 */
export function showsPollingOffNote(
  kind: string,
  metrics: Metrics,
  polling: "enabled" | "disabled" | undefined,
): boolean {
  return (
    kind === "provider_incident" && polling === "disabled" && text(metrics, "incident_id") === null
  );
}
