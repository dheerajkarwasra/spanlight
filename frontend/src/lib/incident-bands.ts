import type { ProviderIncident } from "@/lib/api";

/** One provider incident as a shaded span of a time chart, already clipped to the chart. */
export interface Band {
  start: Date;
  end: Date;
  /** The status page's title, rendered as text only. */
  title: string;
  /** Hatched on the chart, so "critical" is not carried by colour alone. */
  critical: boolean;
}

export interface BandWindow {
  start: Date;
  end: Date;
}

function parse(iso: string): Date | null {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date;
}

/**
 * The incidents that overlap `window`, clipped to it on both sides. An open incident (no
 * `resolved_at`) runs to the window's end; one whose times do not parse is skipped. Callers pick
 * which incidents to pass (the overview passes the API-affected ones of the providers it shows).
 */
export function clipBands(incidents: readonly ProviderIncident[], window: BandWindow): Band[] {
  const bands: Band[] = [];
  for (const incident of incidents) {
    const started = parse(incident.started_at);
    const resolved = incident.resolved_at === null ? window.end : parse(incident.resolved_at);
    if (started === null || resolved === null) {
      continue;
    }
    if (started >= window.end || resolved <= window.start) {
      continue;
    }
    bands.push({
      start: started < window.start ? window.start : started,
      end: resolved > window.end ? window.end : resolved,
      title: incident.title,
      critical: incident.impact === "critical",
    });
  }
  return bands;
}
