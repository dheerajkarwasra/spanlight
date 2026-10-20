import { ChevronDown, Download } from "lucide-react";

import { Callout } from "@/components/callout";
import { DisabledReason } from "@/components/disabled-reason";
import { SectionCard } from "@/components/section-card";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { AuditFileFormat } from "@/lib/api";

import { AuditFilterBar } from "./audit-filter-bar";
import { AuditIntegrity } from "./audit-integrity";
import { AuditLogContent } from "./audit-log-content";
import { useAuditLogQuery } from "./audit-queries";
import { AuditStreamCard } from "./audit-stream-card";
import { useAuditDownload } from "./use-audit-download";
import { useAuditFilters } from "./use-audit-filters";

/** A role without `audit:read` never gets here: `SettingsLayout` shows the no-access state. */
export function AuditLogPage() {
  return (
    <div className="flex flex-col gap-6">
      <AuditLogCard />
      <AuditStreamCard />
    </div>
  );
}

/** The audit log with its filters. The list and the downloads are asked for the same ones. */
function AuditLogCard() {
  const { values, filters, hasFilters, setFilters, clearFilters } = useAuditFilters();
  const query = useAuditLogQuery(filters);
  const file = useAuditDownload(filters);

  const isEmpty = query.isSuccess && query.data.pages[0]?.items.length === 0;
  const noMatch = hasFilters && isEmpty;
  // Nothing to download while the answer is an error or empty; while loading it stays available.
  let downloadBlocked: string | null = null;
  if (query.isError && !query.isFetchNextPageError) {
    downloadBlocked = "Download is available once the audit log loads.";
  } else if (isEmpty) {
    downloadBlocked = "There are no events to download.";
  }

  return (
    <SectionCard
      title="Audit log"
      description="Who changed what in this organization, sign-ins and refused changes included. Newest first."
      actions={
        <DownloadMenu
          blockedReason={downloadBlocked}
          loading={file.isPending}
          onDownload={file.download}
        />
      }
      className="gap-3"
    >
      <AuditFilterBar
        values={values}
        onChange={setFilters}
        showClear={hasFilters && !noMatch}
        onClear={clearFilters}
      />
      {file.tooLarge ? (
        <Callout tone="warning" role="alert" title="Too many events to download">
          This file would have more than 50,000 events. Narrow the date range and download again.
        </Callout>
      ) : null}
      <AuditLogContent query={query} filtered={hasFilters} onClearFilters={clearFilters} />
      <AuditIntegrity />
    </SectionCard>
  );
}

interface DownloadMenuProps {
  /** Why the button is off, or null when it can be used. */
  blockedReason: string | null;
  loading: boolean;
  onDownload: (format: AuditFileFormat) => void;
}

const FORMATS: { format: AuditFileFormat; label: string; hint: string }[] = [
  { format: "csv", label: "CSV", hint: "For spreadsheets" },
  { format: "jsonl", label: "JSON Lines", hint: "One event per line, for log tools" },
];

/** Figma "Audit log v3": Download opens a menu of the two file formats. */
function DownloadMenu({ blockedReason, loading, onDownload }: DownloadMenuProps) {
  const button = (
    <Button
      variant="secondary"
      className="pr-3 pl-3.5 [&_svg]:size-4"
      disabled={blockedReason !== null}
      loading={loading}
    >
      {loading ? null : <Download aria-hidden />}
      Download
      <ChevronDown aria-hidden className="text-muted-foreground" />
    </Button>
  );
  if (blockedReason !== null) {
    return <DisabledReason reason={blockedReason}>{button}</DisabledReason>;
  }
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>{button}</DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-60">
        {FORMATS.map(({ format, label, hint }) => (
          <DropdownMenuItem
            key={format}
            className="flex-col items-start gap-px py-2"
            onSelect={() => {
              onDownload(format);
            }}
          >
            <span className="text-sm font-semibold text-foreground">{label}</span>
            <span className="text-xs font-medium text-muted-foreground">{hint}</span>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
