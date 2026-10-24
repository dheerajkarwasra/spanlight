import { getRouteApi, Link } from "@tanstack/react-router";
import { FileText, Info, Search } from "lucide-react";
import { useEffect, useState } from "react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { ListFooter } from "@/components/list-footer";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { LiveDemoButton } from "@/features/auth";
import { KeySnippet } from "@/features/onboarding";
import { usePermission } from "@/features/shell";
import { formatInteger, pluralNoun } from "@/lib/format";

import { NewPromptEditor } from "./new-prompt-editor";
import { PromptList, PromptListSkeleton } from "./prompt-list";
import { usePromptListQuery } from "./prompt-queries";

const promptsRoute = getRouteApi("/_authed/$orgId/$projectId/prompts");

/** The Python SDK's "Prompts" section: how to fetch a prompt by label and link spans to it. */
const SDK_DOCS_URL =
  "https://github.com/dheerajkarwasra/spanlight/blob/main/sdks/python/README.md#prompts";
const DESCRIPTION =
  "Versioned prompts that your traces link back to. Edit once, then roll out with a label.";

/** `/prompts`: the list, or the new-prompt editor while `?create` is set. */
export function PromptsPage() {
  const { create } = promptsRoute.useSearch();
  const canWrite = usePermission("prompts:write");
  if (create && canWrite) {
    return <NewPromptEditor />;
  }
  return <PromptListPage canWrite={canWrite} />;
}

function useDebounced(value: string, delayMs: number): string {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(timer);
  }, [value, delayMs]);
  return debounced;
}

function PromptListPage({ canWrite }: { canWrite: boolean }) {
  const [search, setSearch] = useState("");
  const q = useDebounced(search.trim(), 250);
  const listQuery = usePromptListQuery(q);
  const prompts = listQuery.data?.pages.flatMap((page) => page.items) ?? [];
  const stale = listQuery.isPlaceholderData;
  const firstLoad = listQuery.isPending || (listQuery.isError && prompts.length === 0);
  const empty = !firstLoad && prompts.length === 0 && q === "";

  const count = `${formatInteger(prompts.length) ?? ""}${listQuery.hasNextPage ? "+" : ""}`;
  const production = prompts.filter((prompt) =>
    prompt.labels.some((label) => label.label === "production"),
  ).length;
  const subtitle = listQuery.isError
    ? "Count unavailable"
    : `${count} ${pluralNoun(prompts.length, "prompt")} · ${String(production)} labelled production`;

  function renderBody() {
    if (listQuery.isPending) {
      return <PromptListSkeleton />;
    }
    if (listQuery.isError && prompts.length === 0) {
      return (
        <div className="rounded-tile bg-surface-muted">
          <ErrorState
            error={listQuery.error}
            title="Couldn't load prompts"
            onRetry={() => void listQuery.refetch()}
          />
        </div>
      );
    }
    if (prompts.length === 0) {
      return (
        <p className="rounded-tile bg-surface-muted px-5 py-8 text-center text-sm text-muted-foreground">
          No prompt name starts with “{q}”. Names are case-sensitive.
        </p>
      );
    }
    return (
      <>
        <PromptList prompts={prompts} stale={stale} />
        {listQuery.isFetchNextPageError ? (
          <ErrorState
            compact
            error={listQuery.error}
            title="Couldn't load more prompts"
            onRetry={() => void listQuery.fetchNextPage()}
          />
        ) : (
          <ListFooter
            layout="card"
            summary={`Showing ${count} ${pluralNoun(prompts.length, "prompt")}`}
            hasNextPage={listQuery.hasNextPage && !stale}
            isFetchingNextPage={listQuery.isFetchingNextPage}
            onLoadMore={() => void listQuery.fetchNextPage()}
          />
        )}
        <p className="flex items-center gap-2 px-1 py-1.5 text-xs font-medium text-muted-foreground">
          <Info aria-hidden className="size-3.5 shrink-0" />
          Versions are immutable. Labels such as production and staging point at a version and can
          move.
        </p>
      </>
    );
  }

  return (
    <div className="flex flex-col gap-4 md:gap-6">
      <PageHeader
        title="Prompts"
        description={DESCRIPTION}
        actions={canWrite && !empty ? <NewPromptButton label="New prompt" /> : null}
      />
      <Card className="flex flex-col gap-4 p-4 sm:p-6" aria-busy={listQuery.isPending}>
        {empty ? (
          <EmptyState
            icon={FileText}
            title="No prompts yet"
            description="Create one here, or push one from the SDK, and every trace will link back to the version that produced it."
            action={
              <>
                {canWrite ? <NewPromptButton label="Create prompt" /> : null}
                <Button asChild>
                  <a href={SDK_DOCS_URL} target="_blank" rel="noreferrer">
                    Push from the SDK
                    <span className="sr-only"> (opens in a new tab)</span>
                  </a>
                </Button>
              </>
            }
            setup={
              <>
                <KeySnippet code={(keyLine) => `${keyLine}  # your key from Settings › API keys`} />
                <LiveDemoButton />
              </>
            }
          />
        ) : (
          <>
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
              <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                <h2 className="text-base font-bold text-foreground">All prompts</h2>
                <p className="text-xs font-medium text-muted-foreground" aria-live="polite">
                  {listQuery.isPending ? "Loading prompts" : subtitle}
                </p>
              </div>
              <label className="relative sm:w-56">
                <span className="sr-only">Search prompts</span>
                <Search
                  aria-hidden
                  className="pointer-events-none absolute top-1/2 left-3.5 size-3.5 -translate-y-1/2 text-muted-foreground"
                />
                <Input
                  type="search"
                  value={search}
                  placeholder="Search prompts"
                  onChange={(event) => setSearch(event.target.value)}
                  className="h-9 rounded-full pl-9 text-[13px]"
                />
              </label>
            </div>
            {renderBody()}
          </>
        )}
      </Card>
    </div>
  );
}

function NewPromptButton({ label }: { label: string }) {
  const { orgId, projectId } = promptsRoute.useParams();
  return (
    <Button variant="primary" asChild>
      <Link
        to="/$orgId/$projectId/prompts"
        params={{ orgId, projectId }}
        search={(previous) => ({ ...previous, create: true })}
      >
        {label}
      </Link>
    </Button>
  );
}
