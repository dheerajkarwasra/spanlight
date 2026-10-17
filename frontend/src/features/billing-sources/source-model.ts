import type { BillingProvider, BillingSource, BillingSourceKind } from "@/lib/api";
import { pluralize } from "@/lib/format";

/** The kind as the list's second line names it ("Admin API · anthropic"). */
const KIND_SHORT_LABELS: Record<BillingSourceKind, string> = {
  anthropic_admin_api: "Admin API",
  openai_costs_api: "Admin API",
  csv_upload: "CSV upload",
};

/** The create dialog's kind cards (Figma "Settings/Choice card"). */
export const KIND_CHOICES: readonly {
  value: BillingSourceKind;
  title: string;
  description: string;
}[] = [
  {
    value: "anthropic_admin_api",
    title: "Anthropic Admin API",
    description: "Daily cost and usage report.",
  },
  { value: "openai_costs_api", title: "OpenAI Admin API", description: "Daily costs and usage." },
  {
    value: "csv_upload",
    title: "CSV upload",
    description: "The provider’s cost export. No key stored.",
  },
];

export const PROVIDER_LABELS: Record<BillingProvider, string> = {
  anthropic: "Anthropic",
  openai: "OpenAI",
  google: "Google",
};

const UTC_FORMAT = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
  timeZone: "UTC",
});

/** "Oct 15, 03:31 UTC": fetches run on a UTC schedule, so their times are shown in UTC. */
export function formatUtc(iso: string): string {
  return `${UTC_FORMAT.format(new Date(iso))} UTC`;
}

/** "just now", "2 min ago", "3 h ago", "3 d ago": short enough for the 190 px status column. */
export function compactAgo(iso: string, now: Date = new Date()): string {
  const seconds = Math.max(0, (now.getTime() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) {
    return "just now";
  }
  if (seconds < 3600) {
    return `${String(Math.floor(seconds / 60))} min ago`;
  }
  if (seconds < 86_400) {
    return `${String(Math.floor(seconds / 3600))} h ago`;
  }
  return `${String(Math.floor(seconds / 86_400))} d ago`;
}

/** "Admin API · anthropic", with "· turned off" for a disabled source. */
export function sourceMeta(source: BillingSource): string {
  const meta = `${KIND_SHORT_LABELS[source.kind]} · ${source.provider}`;
  return source.enabled ? meta : `${meta} · turned off`;
}

export function projectsLabel(count: number): string {
  return pluralize(count, "project");
}

export type StatusTone = "success" | "danger" | "neutral";

export interface StatusView {
  tone: StatusTone;
  /** The chip's text. */
  label: string;
  /** What the chip's tooltip explains. */
  tooltip: string;
  /** The chip's accessible name (Figma annotation "Status: Failing, 401 Unauthorized"). */
  accessibleName: string;
}

function okView(source: BillingSource, now: Date): StatusView {
  const csv = source.kind === "csv_upload";
  const fetchedAt = source.last_fetched_at;
  if (fetchedAt === null) {
    return {
      tone: "success",
      label: "OK",
      tooltip: "The last fetch worked.",
      accessibleName: "Status: OK",
    };
  }
  const ago = compactAgo(fetchedAt, now);
  const verb = csv ? "uploaded" : "fetched";
  return {
    tone: "success",
    label: `OK · ${verb} ${ago}`,
    tooltip: `Last ${verb} ${formatUtc(fetchedAt)}`,
    accessibleName: `Status: OK, ${verb} ${ago}`,
  };
}

/** The status chip of a source: `ok`, `failing` with its status line, or `never fetched`. */
export function statusView(source: BillingSource, now: Date = new Date()): StatusView {
  switch (source.status) {
    case "ok":
      return okView(source, now);
    case "failing": {
      const line = source.last_error;
      return {
        tone: "danger",
        label: line ? `Failing · ${line}` : "Failing",
        tooltip: line ? `The last fetch failed: ${line}` : "The last fetch failed.",
        accessibleName: line ? `Status: Failing, ${line}` : "Status: Failing",
      };
    }
    case "never_fetched":
      return {
        tone: "neutral",
        label: "Never fetched",
        tooltip:
          source.kind === "csv_upload"
            ? "Nothing uploaded yet. Upload the provider's cost export to compare it."
            : "The first fetch runs at 03:30 UTC, or now with Refresh.",
        accessibleName: "Status: Never fetched",
      };
  }
}

/** The line under the chip: when the last successful fetch or upload finished. */
export function lastSuccessLabel(source: BillingSource): string {
  const csv = source.kind === "csv_upload";
  if (source.last_success_at === null) {
    return csv ? "No upload yet" : "No successful fetch yet";
  }
  return `${csv ? "Last upload" : "Last success"} ${formatUtc(source.last_success_at)}`;
}
