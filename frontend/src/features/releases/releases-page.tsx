import { getRouteApi } from "@tanstack/react-router";

import { Notice } from "@/components/notice";
import { PageHeader } from "@/components/page-header";
import { isApiError } from "@/lib/api";

import { CompareBody } from "./compare-body";
import { defaultPair } from "./default-pair";
import { FailureCategoriesCard } from "./failure-categories-card";
import { ReleasePicker } from "./release-picker";
import { useReleaseCompareQuery, useReleaseListQuery } from "./releases-queries";
import { ReleasesTable } from "./releases-table";

const releasesRoute = getRouteApi("/_authed/$orgId/$projectId/releases");

const WINDOW_NOTICE = "Narrow the range to 30 days to compare releases.";

function isWindowTooLarge(error: unknown): boolean {
  return isApiError(error) && error.code === "RELEASE_WINDOW_TOO_LARGE";
}

/**
 * Figma "Releases — Compare": pick a baseline and a candidate (previous vs latest by default), then
 * see what changed; below, the window's releases with links to their traces, and the failure
 * categories' rates across them.
 */
export function ReleasesPage() {
  const search = releasesRoute.useSearch();
  const navigate = releasesRoute.useNavigate();
  const list = useReleaseListQuery();
  // Without a pair in the URL: the previous release against the latest.
  const { a, b, derived } = defaultPair(list.data ?? [], search.a, search.b);
  // A pair derived from the previous window's list would be compared in the new window.
  const settled = !(derived && list.isPlaceholderData);
  const compare = useReleaseCompareQuery(a, b, list.isSuccess && list.data.length > 0 && settled);
  const tooLarge = isWindowTooLarge(list.error) || isWindowTooLarge(compare.error);

  function commit(nextA: string, nextB: string) {
    void navigate({ search: (prev) => ({ ...prev, a: nextA, b: nextB }) });
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Compare releases"
        description="Pick a baseline and a candidate to see what changed between two releases."
      />
      {tooLarge ? (
        <Notice tone="warning" role="alert">
          {WINDOW_NOTICE}
        </Notice>
      ) : null}
      <ReleasePicker
        key={`${a ?? ""}|${b ?? ""}`}
        releases={list.data}
        failed={list.isError}
        a={a}
        b={b}
        unavailable={tooLarge}
        onCompare={commit}
      />
      {tooLarge ? null : (
        <>
          <CompareBody list={list} compare={compare} a={a} b={b} />
          {list.data !== undefined && list.data.length > 0 ? (
            <ReleasesTable releases={list.data} stale={list.isPlaceholderData} />
          ) : null}
          <FailureCategoriesCard />
        </>
      )}
    </div>
  );
}
