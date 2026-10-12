import { Link } from "@tanstack/react-router";
import { ChevronRight } from "lucide-react";

import { RelativeTime } from "@/components/relative-time";
import { Skeleton } from "@/components/ui/skeleton";
import { useProjectParams } from "@/features/shell";
import { toPathPromptName, type PromptListItem } from "@/lib/api";
import { cn } from "@/lib/utils";

import { KindChip, LabelBadges } from "./label-badges";

const COLUMNS = "md:grid md:grid-cols-[minmax(0,1fr)_80px_64px_minmax(0,250px)_96px_16px] md:gap-4";

/** Figma "Prompts — List": tile rows on wide screens, stacked tiles at 375 px. */
export function PromptList({ prompts, stale }: { prompts: PromptListItem[]; stale: boolean }) {
  return (
    <div className={cn("flex flex-col gap-1.5", stale && "opacity-60 transition-opacity")}>
      <div
        aria-hidden
        className={cn(
          COLUMNS,
          "hidden px-5 py-2 text-overline font-semibold text-subtle-foreground uppercase",
        )}
      >
        <span>Name</span>
        <span>Kind</span>
        <span>Latest</span>
        <span>Labels</span>
        <span>Updated</span>
      </div>
      <ul className="flex flex-col gap-1.5" aria-label="Prompts">
        {prompts.map((prompt) => (
          <li key={prompt.id}>
            <PromptRow prompt={prompt} />
          </li>
        ))}
      </ul>
    </div>
  );
}

function PromptRow({ prompt }: { prompt: PromptListItem }) {
  const { orgId, projectId } = useProjectParams();
  return (
    <Link
      to="/$orgId/$projectId/prompts/$name"
      params={{ orgId, projectId, name: toPathPromptName(prompt.name) }}
      className={cn(
        COLUMNS,
        "flex flex-col gap-2 rounded-tile bg-surface-muted px-4 py-3.5 transition-colors hover:bg-surface-hover md:items-center md:px-5",
      )}
    >
      <span className="flex min-w-0 flex-col gap-[3px]">
        <span className="flex items-center justify-between gap-2">
          <span className="font-mono text-[13px] [overflow-wrap:anywhere] text-foreground">
            {prompt.name}
          </span>
          <ChevronRight aria-hidden className="size-4 shrink-0 text-muted-foreground md:hidden" />
        </span>
        {prompt.description ? (
          <span className="line-clamp-2 text-xs text-muted-foreground">{prompt.description}</span>
        ) : null}
      </span>
      <span className="flex flex-wrap items-center gap-2 md:contents">
        <span>
          <KindChip kind={prompt.kind} />
        </span>
        <span className="font-mono text-[13px] text-foreground">
          <span className="sr-only">Latest version </span>v{prompt.latest_version}
        </span>
        <LabelBadges labels={prompt.labels} showVersion />
      </span>
      <span className="text-xs font-medium text-muted-foreground">
        <span className="md:sr-only">Updated </span>
        <RelativeTime iso={prompt.updated_at} />
      </span>
      <ChevronRight aria-hidden className="hidden size-4 text-muted-foreground md:block" />
    </Link>
  );
}

export function PromptListSkeleton() {
  return (
    <div className="flex flex-col gap-1.5" aria-hidden>
      {Array.from({ length: 7 }, (_, index) => (
        <div
          key={index}
          className="flex items-center gap-4 rounded-tile bg-surface-muted px-5 py-4"
        >
          <div className="flex flex-1 flex-col gap-2">
            <Skeleton className="h-3.5 w-48 max-w-full bg-surface-hover" />
            <Skeleton className="h-3 w-72 max-w-full bg-surface-hover" />
          </div>
          <Skeleton className="hidden h-5 w-12 rounded-full bg-surface-hover md:block" />
          <Skeleton className="hidden h-5 w-28 rounded-full bg-surface-hover md:block" />
          <Skeleton className="hidden h-3 w-16 bg-surface-hover md:block" />
        </div>
      ))}
    </div>
  );
}
