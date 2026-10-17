import { ExternalLink, Info } from "lucide-react";
import { useId } from "react";

import { ErrorState } from "@/components/error-state";
import { TileListSkeleton } from "@/components/tile-list";
import { Badge } from "@/components/ui/badge";
import { Card, CardTitle } from "@/components/ui/card";
import type { ProviderIncident } from "@/lib/api";
import { statusPageUrl } from "@/lib/status-page-url";
import { cn } from "@/lib/utils";

import { utcMoment } from "./bucket-labels";
import { FeedStatusLine } from "./feed-status-line";
import { useProviderIncidents } from "./providers-queries";

function capitalise(value: string): string {
  return value.length === 0 ? value : `${value[0]?.toUpperCase() ?? ""}${value.slice(1)}`;
}

/** "Major · Resolved · Oct 16, 01:12 – 02:05 UTC", or "… · since Oct 16, 04:40 UTC · ongoing". */
function incidentMeta(incident: ProviderIncident): string {
  const status = capitalise(incident.status.replaceAll("_", " "));
  const started = utcMoment(incident.started_at) ?? "an unknown time";
  const parts = [capitalise(incident.impact), status];
  if (incident.resolved_at === null) {
    parts.push(`since ${started} UTC`, "ongoing");
  } else {
    parts.push(`${started} – ${utcEnd(incident.started_at, incident.resolved_at)} UTC`);
  }
  if (!incident.api_affected) {
    parts.push("not API-affected");
  }
  return parts.join(" · ");
}

function utcDay(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toISOString().slice(0, 10);
}

/** The end time alone on the start's UTC day, else with its own date (a multi-day incident). */
function utcEnd(startedAt: string, resolvedAt: string): string {
  const end = utcMoment(resolvedAt) ?? "an unknown time";
  return utcDay(startedAt) === utcDay(resolvedAt) ? end.replace(/^.*, /, "") : end;
}

function IncidentRow({ incident }: { incident: ProviderIncident }) {
  const muted = !incident.api_affected;
  const url = statusPageUrl(incident.url);
  return (
    <li
      className={cn(
        "flex flex-wrap items-center gap-x-3 gap-y-2 rounded-tile bg-surface-muted px-4 py-3",
        muted && "text-subtle-foreground",
      )}
    >
      <div className="flex min-w-[min(100%,260px)] flex-1 flex-col gap-1">
        <p className="flex items-start gap-2 text-sm">
          <span className={cn("font-mono text-[13px]", !muted && "text-muted-foreground")}>
            {incident.provider}
          </span>
          <span className={cn("min-w-0 flex-1 font-semibold", !muted && "text-foreground")}>
            {incident.title}
          </span>
        </p>
        <p className={cn("text-xs font-medium", !muted && "text-muted-foreground")}>
          {incidentMeta(incident)}
        </p>
      </div>
      <Badge variant={muted ? "neutral" : incident.impact === "critical" ? "danger" : "warning"}>
        {muted ? "Not API" : capitalise(incident.impact)}
      </Badge>
      {url ? (
        <a
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          className={cn(
            "inline-flex min-h-11 items-center gap-1 rounded-sm text-sm font-medium hover:underline hover:underline-offset-4 sm:min-h-0",
            muted ? "text-subtle-foreground" : "text-accent",
          )}
        >
          Status page
          <ExternalLink aria-hidden className="size-3.5" />
          <span className="sr-only">(opens in a new tab)</span>
        </a>
      ) : null}
    </li>
  );
}

/**
 * Figma "Provider incidents card": the status page incidents overlapping the window (greyed when
 * they did not touch the API) and each feed's poll state; with polling off, one line saying so.
 */
export function IncidentList({ now }: { now: Date }) {
  const titleId = useId();
  const incidents = useProviderIncidents();
  const data = incidents.data;

  return (
    <Card role="region" aria-labelledby={titleId} className="flex flex-col gap-4 p-5 sm:p-6">
      <div className="flex flex-col gap-1">
        <CardTitle id={titleId}>Provider incidents</CardTitle>
        <p className="text-xs font-medium text-muted-foreground">
          From the providers’ status pages. Incidents explain errors; nothing is hidden
          <span className="max-sm:hidden"> or resolved</span> because of them.
        </p>
      </div>
      {incidents.isPending ? (
        <TileListSkeleton label="Loading provider incidents" rows={2} className="h-[66px]" />
      ) : incidents.isError ? (
        <ErrorState
          compact
          error={incidents.error}
          onRetry={() => void incidents.refetch()}
          title="Couldn't load provider incidents"
        />
      ) : data?.polling === "disabled" ? (
        <PollingOff />
      ) : (
        <>
          {data && data.items.length > 0 ? (
            <ul aria-label="Provider incidents" className="flex flex-col gap-4">
              {data.items.map((incident) => (
                <IncidentRow key={incident.id} incident={incident} />
              ))}
            </ul>
          ) : (
            <p className="rounded-tile bg-surface-muted px-4 py-6 text-center text-sm text-muted-foreground">
              No provider incidents overlapped this window
            </p>
          )}
          <FeedStatusLine sources={data?.sources ?? []} now={now} />
        </>
      )}
    </Card>
  );
}

/** Frame `Providers/Page/PollingOff`: the incidents section replaced by one plain line. */
function PollingOff() {
  return (
    <div className="flex items-start gap-3 rounded-tile bg-surface-muted px-4 py-3.5">
      <Info aria-hidden className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
      <div className="flex min-w-0 flex-col gap-1 [overflow-wrap:anywhere]">
        <p className="text-sm text-foreground">
          Provider status feeds are off on this server (
          <code className="font-mono text-[13px]">PROVIDER_STATUS_POLLING_ENABLED=false</code>)
        </p>
        <p className="text-xs font-medium text-muted-foreground">
          Errors, rate-limit kinds and retries above come from your own spans and are unaffected. An
          administrator can turn the feeds on.
        </p>
      </div>
    </div>
  );
}
