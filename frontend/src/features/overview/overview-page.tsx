import { ErrorState } from "@/components/error-state";
import { Card } from "@/components/ui/card";
import { useMe } from "@/features/auth/queries";
import { useProjectQuery } from "@/features/shell/project-context";
import { describeRange } from "@/lib/time-range";

import { EstimatedChip } from "./estimated-chip";
import { FirstRun } from "./first-run";
import { formatHeroDate, greetingFor, joinEyebrow, shortRangeLabel } from "./hero";
import { EmptyRangeState } from "./overview-empty-states";
import { OverviewDashboard, OverviewSkeleton } from "./overview-dashboard";
import { OverviewHero } from "./overview-hero";
import { pageStateOf, statusFor, type PageState } from "./page-state";
import { useOverviewQueries, type OverviewQueries } from "./use-overview-queries";

export function OverviewPage() {
  const queries = useOverviewQueries();
  const me = useMe();
  const project = useProjectQuery();
  const now = new Date();
  const state = pageStateOf(queries);
  const { range, environment } = queries;
  const environmentLabel = environment ?? "All environments";

  const date = formatHeroDate(now);
  // A window that reads any rollup hour (the default 24 h does) estimates percentiles: the chip
  // explains the "≈" on percentile values. With nothing to show in the window there are no
  // percentiles, so no chip either. While the previous range is held as placeholder data, its flag
  // says nothing about the new window.
  const { overview } = queries;
  const estimated =
    state === "ready" && !overview.isPlaceholderData && overview.data?.approximate === true;
  const eyebrow =
    state === "first-run"
      ? {
          full: joinEyebrow([date, project.data?.name, environmentLabel]),
          compact: joinEyebrow([date, project.data?.name]),
        }
      : {
          full: joinEyebrow([date, environmentLabel, describeRange(range)]),
          compact: joinEyebrow([date, shortRangeLabel(range)]),
          trailing: estimated ? <EstimatedChip /> : null,
        };

  return (
    <div className="flex flex-col gap-8">
      <OverviewHero
        eyebrow={eyebrow}
        greeting={greetingFor(now, me.user.name)}
        status={statusFor(state, queries)}
        isLoading={state === "loading"}
        listeningEnvironment={environment ?? null}
      />
      <OverviewBody
        state={state}
        queries={queries}
        projectName={project.data?.name ?? null}
        now={now}
      />
      {/* Announce loading once for assistive tech; the skeletons themselves are aria-hidden. */}
      <p className="sr-only" role="status" aria-live="polite">
        {state === "loading" ? "Loading overview" : ""}
      </p>
    </div>
  );
}

interface OverviewBodyProps {
  state: PageState;
  queries: OverviewQueries;
  projectName: string | null;
  now: Date;
}

function OverviewBody({ state, queries, projectName, now }: OverviewBodyProps) {
  const { overview, range, environment } = queries;

  switch (state) {
    case "loading":
      return <OverviewSkeleton />;
    case "error":
      return (
        <Card>
          <ErrorState
            error={overview.error}
            title="Couldn't load the overview"
            onRetry={() => {
              void overview.refetch();
            }}
          />
        </Card>
      );
    case "first-run":
      return <FirstRun projectName={projectName} environment={environment} />;
    case "empty-window":
      return <EmptyRangeState range={range} environment={environment} />;
    case "ready":
      return <OverviewDashboard queries={queries} now={now} />;
  }
}
