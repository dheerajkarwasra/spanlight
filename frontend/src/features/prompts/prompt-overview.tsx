import { Link } from "@tanstack/react-router";
import { Ellipsis } from "lucide-react";
import { useState } from "react";

import { ErrorState } from "@/components/error-state";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { useProjectParams } from "@/features/shell";
import type { Prompt } from "@/lib/api";

import { CardTitle } from "./card-title";
import { DeletePromptDialog } from "./delete-prompt-dialog";
import { KindChip } from "./label-badges";
import { LabelDialog } from "./label-dialog";
import { useAuthorName } from "./use-author-name";
import { TemplateCard } from "./template-card";
import { UsageCard } from "./usage-card";
import { usePromptLink } from "./use-prompt-links";
import { usePromptVersionQuery, usePromptVersionsQuery } from "./prompt-queries";
import { VersionList } from "./version-list";

interface PromptOverviewProps {
  prompt: Prompt;
  /** The version shown in the usage and template cards. */
  selected: number;
  onSelect: (version: number) => void;
  canWrite: boolean;
}

/** Figma "Prompts — Detail": versions on the left, usage and template of one version on the right. */
export function PromptOverview({ prompt, selected, onSelect, canWrite }: PromptOverviewProps) {
  const [labelVersion, setLabelVersion] = useState<number | null>(null);
  const versions = usePromptVersionsQuery(prompt.name).data?.pages.flatMap((page) => page.items);
  const authorName = useAuthorName();
  const isLatest = selected === prompt.latest.version;
  // The same cache entry the template card reads.
  const selectedVersion = usePromptVersionQuery(prompt.name, isLatest ? null : selected).data;

  return (
    <>
      <PromptHeader prompt={prompt} selected={selected} canWrite={canWrite} />
      <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_420px] lg:gap-6">
        <VersionList
          name={prompt.name}
          labels={prompt.labels}
          latest={prompt.latest.version}
          selected={selected}
          onSelect={onSelect}
          onSetLabel={canWrite ? setLabelVersion : null}
          authorName={authorName}
        />
        <div className="flex min-w-0 flex-col gap-4 lg:gap-6">
          <UsageCard
            name={prompt.name}
            version={selected}
            versionId={isLatest ? prompt.latest.id : (selectedVersion?.id ?? null)}
          />
          <SelectedTemplate prompt={prompt} selected={selected} />
        </div>
      </div>
      <LabelDialog
        name={prompt.name}
        labels={prompt.labels}
        versions={versions ?? [prompt.latest]}
        version={labelVersion}
        onClose={() => setLabelVersion(null)}
      />
    </>
  );
}

function SelectedTemplate({ prompt, selected }: { prompt: Prompt; selected: number }) {
  const isLatest = selected === prompt.latest.version;
  const query = usePromptVersionQuery(prompt.name, isLatest ? null : selected);
  const version = isLatest ? prompt.latest : query.data;
  if (version) {
    return <TemplateCard version={version} />;
  }
  return (
    <Card className="flex flex-col gap-4 p-5 sm:p-6" aria-busy={query.isPending}>
      <CardTitle title={`Template · v${String(selected)}`} />
      {query.isError ? (
        <div className="rounded-tile bg-surface-muted">
          <ErrorState
            compact
            error={query.error}
            title="Couldn't load this version"
            onRetry={() => void query.refetch()}
          />
        </div>
      ) : (
        <div role="status" aria-label={`Loading v${String(selected)}`}>
          <Skeleton className="h-40 rounded-input" />
        </div>
      )}
    </Card>
  );
}

interface PromptHeaderProps {
  prompt: Prompt;
  selected: number;
  canWrite: boolean;
}

function PromptHeader({ prompt, selected, canWrite }: PromptHeaderProps) {
  const { orgId, projectId } = useProjectParams();
  const [deleting, setDeleting] = useState(false);
  const editLink = usePromptLink(prompt.name, { view: "edit", v: selected });
  return (
    <>
      <PageHeader
        title={
          <span className="inline-flex flex-wrap items-center gap-x-3 gap-y-1">
            {prompt.name}
            <KindChip kind={prompt.kind} className="align-middle" />
          </span>
        }
        description={prompt.description ?? undefined}
        actions={
          <>
            {canWrite ? (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    size="icon"
                    aria-label={`More actions for ${prompt.name}`}
                    className="size-11 border-border-strong shadow-none sm:size-10"
                  >
                    <Ellipsis aria-hidden />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="w-48">
                  <DropdownMenuItem destructive onSelect={() => setDeleting(true)}>
                    Delete prompt
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            ) : null}
            <Button asChild>
              <Link
                to="/$orgId/$projectId/playground"
                params={{ orgId, projectId }}
                search={{ prompt: prompt.name, v: selected }}
              >
                Open in playground
              </Link>
            </Button>
            {canWrite ? (
              <Button variant="primary" asChild>
                <Link {...editLink}>New version</Link>
              </Button>
            ) : null}
          </>
        }
      />
      <DeletePromptDialog
        name={prompt.name}
        versionCount={prompt.latest.version}
        labelCount={prompt.labels.length}
        open={deleting}
        onOpenChange={setDeleting}
      />
    </>
  );
}
