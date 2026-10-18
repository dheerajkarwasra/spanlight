import type { Bucket, ModelMetrics, ProviderIncident } from "@/lib/api";
import { clipBands, type Band, type BandWindow } from "@/lib/incident-bands";
import { feedLabel } from "@/lib/provider-label";

import { BUCKET_MS, type ChartPoint } from "./chart-data";

/**
 * The providers the project called in the window, lowercased: span providers keep their
 * sender's case and the status feeds' are lowercase (the backend matches them the same way).
 */
export function usedProviders(models: readonly ModelMetrics[]): Set<string> {
  const providers = new Set<string>();
  for (const model of models) {
    if (model.provider !== null) {
      providers.add(model.provider.toLowerCase());
    }
  }
  return providers;
}

/** "OpenAI · Elevated error rates across the API (critical)": the title every surface shows. */
function incidentLabel(incident: ProviderIncident): string {
  const provider = feedLabel(incident.provider);
  const impact = incident.impact.trim();
  return `${provider} · ${incident.title}${impact ? ` (${impact})` : ""}`;
}

/** The API-affected incidents of the providers the project used, labelled and clipped. */
export function overviewBands(
  incidents: readonly ProviderIncident[],
  providers: ReadonlySet<string>,
  window: BandWindow,
): Band[] {
  const shown = incidents
    .filter((incident) => incident.api_affected && providers.has(incident.provider.toLowerCase()))
    .map((incident) => ({ ...incident, title: incidentLabel(incident) }));
  return clipBands(shown, window);
}

/** A band as the categorical x axis places it: from one bucket's point to another's. */
export interface BandSpan {
  key: string;
  x1: string;
  x2: string;
  critical: boolean;
}

/**
 * Each band from the bucket holding its start to the first bucket starting at or after its end
 * (the last bucket when none does). A point marks its bucket's start, so this covers the hours
 * the incident touched. A band that starts in the newest bucket has no later point to reach, so it
 * starts at the previous point instead. Bands outside every bucket are skipped.
 */
export function bandSpans(
  bands: readonly Band[],
  points: readonly ChartPoint[],
  bucket: Bucket,
): BandSpan[] {
  const starts = points.map((point) => Date.parse(point.bucketStart));
  const first = starts[0];
  const lastIndex = starts.length - 1;
  const last = starts[lastIndex];
  if (first === undefined || last === undefined) {
    return [];
  }
  const size = BUCKET_MS[bucket];
  return bands.flatMap((band, index) => {
    const start = band.start.getTime();
    const end = band.end.getTime();
    if (end <= first || start >= last + size) {
      return [];
    }
    const from = Math.max(
      0,
      starts.findIndex((bucketStart) => bucketStart + size > start),
    );
    const found = starts.findIndex((bucketStart) => bucketStart >= end);
    const to = found === -1 ? lastIndex : found;
    // A band inside the newest bucket ("ongoing this hour") would span one point and draw nothing:
    // widen it back to the previous bucket's point so it stays visible.
    const x1 = points[to <= from && from > 0 ? from - 1 : from]?.bucketStart;
    const x2 = points[to]?.bucketStart;
    return x1 === undefined || x2 === undefined
      ? []
      : [{ key: String(index), x1, x2, critical: band.critical }];
  });
}

function uniqueTitles(bands: readonly Band[]): string[] {
  return [...new Set(bands.map((band) => band.title))];
}

/** The titles of the incidents overlapping one bucket, for its tooltip and table row. */
export function incidentsAt(bands: readonly Band[], bucketStart: string, bucket: Bucket): string[] {
  const start = Date.parse(bucketStart);
  const end = start + BUCKET_MS[bucket];
  return uniqueTitles(
    bands.filter((band) => band.start.getTime() < end && band.end.getTime() > start),
  );
}

/** "2 provider incidents: A; B" for the chart's accessible name; "" without incidents. */
export function incidentSummary(bands: readonly Band[]): string {
  const titles = uniqueTitles(bands);
  if (titles.length === 0) {
    return "";
  }
  const noun = titles.length === 1 ? "provider incident" : "provider incidents";
  return `${String(titles.length)} ${noun}: ${titles.join("; ")}`;
}
