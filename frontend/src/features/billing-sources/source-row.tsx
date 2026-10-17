import { Link } from "@tanstack/react-router";
import { Pencil, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useProjectParams } from "@/features/shell";
import type { BillingSource } from "@/lib/api";

import { CreateSourceDialog } from "./create-source-dialog";
import { DeleteSourceDialog } from "./delete-source-dialog";
import { RefreshButton } from "./refresh-button";
import { ICON_ACTION_CLASS, SOURCE_COLUMNS, TEXT_ACTION_CLASS } from "./source-columns";
import { lastSuccessLabel, projectsLabel, sourceMeta } from "./source-model";
import { SourceStatus } from "./source-status";
import { UploadCsvDialog } from "./upload-csv-dialog";

interface SourceRowProps {
  source: BillingSource;
  /** Admins and owners. Everyone else sees the row without its actions. */
  canManage: boolean;
}

/**
 * Figma "Bet1/Source row" and its mobile tile: name with kind and provider, the status chip and
 * last success, the project count, and Refresh (API kinds) or Upload (CSV), Edit and Delete. The
 * name opens the source's reconciliation page.
 */
export function SourceRow({ source, canManage }: SourceRowProps) {
  const { orgId, projectId } = useProjectParams();
  const projects = source.projects.map((project) => project.name).join(", ");

  return (
    <li className="flex flex-col gap-3 rounded-tile bg-surface-muted p-4 @[40rem]:flex-row @[40rem]:items-center @[40rem]:gap-3 @[40rem]:py-3 @[40rem]:pr-3 @[40rem]:pl-4">
      <div className={SOURCE_COLUMNS.name}>
        <p title={source.name} className="truncate text-sm font-semibold text-foreground">
          <Link
            to="/$orgId/$projectId/settings/billing-sources/$sourceId"
            params={{ orgId, projectId, sourceId: source.id }}
            className="rounded-sm underline-offset-4 hover:underline"
          >
            {source.name}
          </Link>
        </p>
        <p className="text-xs font-medium text-muted-foreground">{sourceMeta(source)}</p>
      </div>
      <div className={SOURCE_COLUMNS.status}>
        <SourceStatus source={source} />
        <p className="text-xs font-medium text-muted-foreground">{lastSuccessLabel(source)}</p>
      </div>
      <p className={SOURCE_COLUMNS.projects} title={projects || undefined}>
        <span className="sr-only">Compared with </span>
        {projectsLabel(source.projects.length)}
      </p>
      {canManage ? <SourceActions source={source} /> : null}
    </li>
  );
}

function SourceActions({ source }: { source: BillingSource }) {
  return (
    <div className={SOURCE_COLUMNS.actions}>
      {source.kind === "csv_upload" ? (
        <UploadCsvDialog
          source={source}
          trigger={
            <Button
              variant="secondary"
              aria-label={`Upload a CSV to ${source.name}`}
              className={TEXT_ACTION_CLASS}
            >
              Upload
            </Button>
          }
        />
      ) : (
        <RefreshButton source={source} />
      )}
      <CreateSourceDialog
        source={source}
        trigger={
          <Button
            variant="secondary"
            size="icon-sm"
            aria-label={`Edit ${source.name}`}
            className={ICON_ACTION_CLASS}
          >
            <Pencil aria-hidden />
          </Button>
        }
      />
      <DeleteSourceDialog
        source={source}
        trigger={
          <Button
            variant="secondary"
            size="icon-sm"
            aria-label={`Delete ${source.name}`}
            className={ICON_ACTION_CLASS}
          >
            <Trash2 aria-hidden />
          </Button>
        }
      />
    </div>
  );
}
