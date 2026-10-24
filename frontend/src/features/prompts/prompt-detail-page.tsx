import { getRouteApi } from "@tanstack/react-router";
import { SearchX } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { usePermission } from "@/features/shell";
import { isApiError, type Prompt } from "@/lib/api";

import { PromptsBreadcrumbs } from "./back-link";
import { NewVersionEditor } from "./prompt-editor";
import { PromptOverview } from "./prompt-overview";
import { usePromptQuery, usePromptVersionQuery } from "./prompt-queries";
import { usePromptName } from "./use-prompt-links";
import { VersionDiff } from "./version-diff";

const detailRoute = getRouteApi("/_authed/$orgId/$projectId/prompts/$name");

const PAGE = "flex flex-col gap-4 md:gap-6";

/** `/prompts/$name`: the versions (default), the new-version editor or a diff, by `?view`. */
export function PromptDetailPage() {
  const name = usePromptName();
  const promptQuery = usePromptQuery(name);

  if (promptQuery.isPending) {
    return (
      <div className={PAGE} aria-busy>
        <span className="sr-only" role="status">
          Loading prompt
        </span>
        <PromptsBreadcrumbs current={name} />
        <Skeleton className="h-10 w-72 max-w-full" />
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_420px] lg:gap-6">
          <Skeleton className="h-80 rounded-card" />
          <Skeleton className="h-80 rounded-card" />
        </div>
      </div>
    );
  }
  if (promptQuery.isError) {
    const missing = isApiError(promptQuery.error) && promptQuery.error.isNotFound;
    return (
      <div className={PAGE}>
        <PromptsBreadcrumbs current={name} />
        <Card>
          {missing ? (
            <EmptyState
              icon={SearchX}
              title="Prompt not found"
              description="No prompt in this project has this name. It may have been deleted or renamed."
            />
          ) : (
            <ErrorState
              error={promptQuery.error}
              title="Couldn't load this prompt"
              onRetry={() => void promptQuery.refetch()}
            />
          )}
        </Card>
      </div>
    );
  }
  return (
    <div className={PAGE}>
      <PromptScreen prompt={promptQuery.data} />
    </div>
  );
}

function clampVersion(value: number | undefined, latest: number, fallback: number): number {
  return value !== undefined && value >= 1 && value <= latest ? value : fallback;
}

function PromptScreen({ prompt }: { prompt: Prompt }) {
  const search = detailRoute.useSearch();
  const navigate = detailRoute.useNavigate();
  const canWrite = usePermission("prompts:write");
  const latest = prompt.latest.version;
  const selected = clampVersion(search.v, latest, latest);

  if (search.view === "edit" && canWrite) {
    return <EditFromVersion prompt={prompt} version={selected} />;
  }
  if (search.view === "diff") {
    const to = clampVersion(search.b, latest, selected);
    const from = clampVersion(search.a, latest, Math.max(1, to - 1));
    return <VersionDiff prompt={prompt} from={from} to={to} canWrite={canWrite} />;
  }
  return (
    <>
      <PromptsBreadcrumbs current={prompt.name} />
      <PromptOverview
        prompt={prompt}
        selected={selected}
        canWrite={canWrite}
        onSelect={(version) =>
          void navigate({ search: (previous) => ({ ...previous, v: version }), replace: true })
        }
      />
    </>
  );
}

/** The editor once its starting version is loaded (the latest one is already at hand). */
function EditFromVersion({ prompt, version }: { prompt: Prompt; version: number }) {
  const isLatest = version === prompt.latest.version;
  const query = usePromptVersionQuery(prompt.name, isLatest ? null : version);
  const base = isLatest ? prompt.latest : query.data;
  if (query.isError) {
    return (
      <Card>
        <ErrorState
          error={query.error}
          title="Couldn't load this version"
          onRetry={() => void query.refetch()}
        />
      </Card>
    );
  }
  if (!base) {
    return (
      <div role="status" aria-label="Loading version">
        <Skeleton className="h-96 rounded-card" />
      </div>
    );
  }
  // Keyed, so switching the starting version starts a fresh draft.
  return <NewVersionEditor key={base.id} prompt={prompt} base={base} />;
}
