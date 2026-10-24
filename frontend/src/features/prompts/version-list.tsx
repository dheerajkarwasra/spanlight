import { Link } from "@tanstack/react-router";
import { Ellipsis, FlaskConical, Info } from "lucide-react";

import { ErrorState } from "@/components/error-state";
import { RelativeTime } from "@/components/relative-time";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { usePermission, useProjectParams } from "@/features/shell";
import type { PromptLabel, PromptVersion } from "@/lib/api";
import { pluralize } from "@/lib/format";
import { cn } from "@/lib/utils";

import { CardTitle } from "./card-title";
import { LabelBadge, LatestBadge } from "./label-badges";
import { usePromptVersionsQuery } from "./prompt-queries";
import { useDiffLink } from "./use-prompt-links";
import { VersionUsageLine } from "./version-usage-line";

interface VersionListProps {
  name: string;
  labels: readonly PromptLabel[];
  latest: number;
  selected: number;
  onSelect: (version: number) => void;
  /** Opens the label dialog on this version; null when the role can't move labels. */
  onSetLabel: ((version: number) => void) | null;
  /** Asks to remove a label; null when the role can't move labels. */
  onRemoveLabel: ((label: string) => void) | null;
  authorName: (userId: string | null) => string;
}

/**
 * Figma "Versions": newest first, the shown version highlighted, labels and seven days of usage,
 * Experiment, Diff and Set label per row.
 */
export function VersionList(props: VersionListProps) {
  const versionsQuery = usePromptVersionsQuery(props.name);
  const versions = versionsQuery.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <Card className="flex min-w-0 flex-col gap-4 p-4 sm:p-6" aria-busy={versionsQuery.isPending}>
      <CardTitle
        title="Versions"
        subtitle={`${pluralize(props.latest, "version")} · newest first`}
      />
      {versionsQuery.isPending ? (
        <div className="flex flex-col gap-1.5" role="status" aria-label="Loading versions">
          {Array.from({ length: 3 }, (_, index) => (
            <Skeleton key={index} className="h-[66px] rounded-tile" />
          ))}
        </div>
      ) : versionsQuery.isError && versions.length === 0 ? (
        <div className="rounded-tile bg-surface-muted">
          <ErrorState
            compact
            error={versionsQuery.error}
            title="Couldn't load versions"
            onRetry={() => void versionsQuery.refetch()}
          />
        </div>
      ) : (
        <ul className="flex flex-col gap-1.5" aria-label="Versions">
          {versions.map((version) => (
            <li key={version.id}>
              <VersionRow {...props} version={version} />
            </li>
          ))}
        </ul>
      )}
      {versionsQuery.hasNextPage ? (
        <Button
          className="self-start"
          loading={versionsQuery.isFetchingNextPage}
          onClick={() => void versionsQuery.fetchNextPage()}
        >
          Load older versions
        </Button>
      ) : null}
      <p className="flex items-center gap-2 px-1 text-xs font-medium text-muted-foreground">
        <Info aria-hidden className="size-3.5 shrink-0" />
        Versions are immutable. Saving a change always creates a new version.
      </p>
    </Card>
  );
}

function VersionRow({ version, ...props }: VersionListProps & { version: PromptVersion }) {
  const number = version.version;
  const current = number === props.selected;
  const labels = props.labels.filter((label) => label.version === number);
  // The select button's ::after covers the row; badges, the usage line and actions sit above it.
  return (
    <div
      className={cn(
        "relative flex items-center gap-3.5 rounded-tile px-4 py-3",
        current ? "bg-surface-selected" : "bg-surface-muted",
      )}
    >
      <div className="flex min-w-0 flex-1 flex-col gap-[3px] pr-12 sm:pr-0">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <button
            type="button"
            aria-pressed={current}
            aria-label={`Show v${String(number)}`}
            onClick={() => props.onSelect(number)}
            className="shrink-0 rounded-sm font-mono text-sm text-foreground after:absolute after:inset-0 after:rounded-tile"
          >
            v{number}
          </button>
          {labels.length > 0 || number === props.latest ? (
            <span className="relative flex flex-wrap items-center gap-1.5">
              {labels.map(({ label }) => (
                <LabelBadge
                  key={label}
                  label={label}
                  onRemove={props.onRemoveLabel ? () => props.onRemoveLabel?.(label) : undefined}
                />
              ))}
              {number === props.latest ? <LatestBadge /> : null}
            </span>
          ) : null}
          <span className="min-w-0 text-sm font-semibold [overflow-wrap:anywhere] text-foreground">
            {version.commit_message ?? (
              <span className="font-normal text-subtle-foreground">No commit message</span>
            )}
          </span>
        </div>
        <span className="relative self-start text-xs text-muted-foreground">
          <VersionUsageLine usage={version.usage_7d ?? null} /> ·{" "}
          {props.authorName(version.created_by)} · <RelativeTime iso={version.created_at} />
        </span>
      </div>
      <VersionActions
        name={props.name}
        versionId={version.id}
        number={number}
        onSetLabel={props.onSetLabel}
      />
    </div>
  );
}

function VersionActions({
  name,
  versionId,
  number,
  onSetLabel,
}: {
  name: string;
  versionId: string;
  number: number;
  onSetLabel: ((version: number) => void) | null;
}) {
  const { orgId, projectId } = useProjectParams();
  const canRun = usePermission("experiments:run");
  const diff = useDiffLink(number - 1, number);
  const experiment = {
    to: "/$orgId/$projectId/experiments/new" as const,
    params: { orgId, projectId },
    search: { prompt: name, version: versionId },
  };
  const hasPrevious = number > 1;
  return (
    <>
      <div className="relative hidden shrink-0 items-center gap-3.5 text-[13px] sm:flex">
        {canRun ? (
          <Link
            {...experiment}
            aria-label={`Create experiment from v${String(number)}`}
            className="inline-flex items-center gap-1 font-semibold text-accent hover:underline"
          >
            <FlaskConical aria-hidden className="size-3.5" />
            Experiment
          </Link>
        ) : null}
        {hasPrevious ? (
          <Link {...diff} className="font-semibold text-accent hover:underline">
            Diff
            <span className="sr-only">
              {" "}
              v{number} with v{number - 1}
            </span>
          </Link>
        ) : null}
        {onSetLabel ? (
          <Button variant="link" className="text-[13px]" onClick={() => onSetLabel(number)}>
            Set label<span className="sr-only"> on v{number}</span>
          </Button>
        ) : null}
      </div>
      {canRun || hasPrevious || onSetLabel ? (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              size="icon"
              className="absolute top-2.5 right-2.5 size-11 border-border-strong shadow-none sm:hidden"
              aria-label={`Actions for v${String(number)}`}
            >
              <Ellipsis aria-hidden />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-52">
            {canRun ? (
              <DropdownMenuItem asChild>
                <Link {...experiment}>Create experiment</Link>
              </DropdownMenuItem>
            ) : null}
            {hasPrevious ? (
              <DropdownMenuItem asChild>
                <Link {...diff}>Diff with previous</Link>
              </DropdownMenuItem>
            ) : null}
            {onSetLabel ? (
              <DropdownMenuItem onSelect={() => onSetLabel(number)}>Set label</DropdownMenuItem>
            ) : null}
          </DropdownMenuContent>
        </DropdownMenu>
      ) : null}
    </>
  );
}
