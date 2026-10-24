import { Info, Pencil, Star, Trash2 } from "lucide-react";
import { useEffect, useRef } from "react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { SectionCard } from "@/components/section-card";
import { ColumnLabels, TileList, TileListSkeleton } from "@/components/tile-list";
import { Button } from "@/components/ui/button";
import { LiveDemoButton } from "@/features/auth";
import { usePermission } from "@/features/shell";
import type { ScoreConfig } from "@/lib/api";
import { cn } from "@/lib/utils";
import { allowedValuesText } from "@/lib/score-format";

import { DeleteScoreConfigDialog } from "./delete-score-config-dialog";
import { FailureCategoriesRow } from "./failure-categories-row";
import { ScoreConfigDialog } from "./score-config-dialog";
import { useScoreConfigsQuery } from "./scores-queries";

/** Column widths once the card is wide enough for a table (Figma "Settings — Score configs"). */
const COLUMNS = {
  name: "min-w-0 @[40rem]:w-[30%]",
  type: "@[40rem]:w-28 shrink-0",
  allowed: "min-w-0 @[40rem]:flex-1",
  origin: "@[40rem]:w-48 shrink-0",
  actions: "w-[76px] shrink-0",
};

/**
 * Settings › Project "Score configs": the type and allowed values of each score name. Members can
 * create, edit and delete configs; everyone in the project can read them.
 */
export function ScoreConfigsCard() {
  const configsQuery = useScoreConfigsQuery();
  const canWrite = usePermission("scores:write");
  const scrolled = useRef(false);

  // The browser jumped to the hash before the anchors below rendered; scroll once they have.
  useEffect(() => {
    if (scrolled.current || !configsQuery.data) {
      return;
    }
    scrolled.current = true;
    const { hash } = window.location;
    if (hash === "#score-configs" || hash === "#failure-categories") {
      document.getElementById(hash.slice(1))?.scrollIntoView();
    }
  }, [configsQuery.data]);

  function body() {
    if (configsQuery.isPending) {
      return <TileListSkeleton label="Loading score configs" rows={3} />;
    }
    if (configsQuery.isError) {
      return (
        <ErrorState
          compact
          error={configsQuery.error}
          title="Couldn't load the score configs"
          onRetry={() => {
            void configsQuery.refetch();
          }}
        />
      );
    }
    if (configsQuery.data.length === 0) {
      return (
        <EmptyState
          icon={Star}
          title="No score configs yet"
          setup={<LiveDemoButton />}
          description="Create one to check score values as they arrive, or send a score from the SDK and its config is created from the first value."
          className="rounded-tile bg-surface-muted py-10"
        />
      );
    }
    return <ConfigList configs={configsQuery.data} canWrite={canWrite} />;
  }

  return (
    <SectionCard
      title="Score configs"
      description="Define the type and range of each score so values are checked when they arrive. A score name with no config gets one from its first value."
      actions={
        canWrite ? (
          <ScoreConfigDialog trigger={<Button variant="secondary">New score config</Button>} />
        ) : null
      }
    >
      {configsQuery.data ? (
        <FailureCategoriesRow configs={configsQuery.data} canWrite={canWrite} />
      ) : null}
      {body()}
      <p className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
        <Info aria-hidden className="size-3.5 shrink-0" />
        Once a config has scores, its type cannot change and it cannot be deleted.
      </p>
    </SectionCard>
  );
}

function ConfigList({ configs, canWrite }: { configs: ScoreConfig[]; canWrite: boolean }) {
  return (
    <div className="@container flex flex-col gap-3">
      <ColumnLabels className="gap-3 pr-3 pl-4 @[40rem]:flex">
        <span className={COLUMNS.name}>Name</span>
        <span className={COLUMNS.type}>Type</span>
        <span className={COLUMNS.allowed}>Allowed values</span>
        <span className={COLUMNS.origin}>Origin</span>
        {canWrite ? <span className={COLUMNS.actions} /> : null}
      </ColumnLabels>
      <TileList label="Score configs">
        {configs.map((config) => (
          <ConfigRow key={config.id} config={config} canWrite={canWrite} />
        ))}
      </TileList>
    </div>
  );
}

function ConfigRow({ config, canWrite }: { config: ScoreConfig; canWrite: boolean }) {
  const allowed = allowedValuesText(config);
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-tile bg-surface-muted py-2.5 pr-3 pl-4 text-sm text-foreground @[40rem]:flex-nowrap">
      <span
        title={config.name}
        className={cn(COLUMNS.name, "truncate font-mono text-label font-semibold")}
      >
        {config.name}
      </span>
      <span className={COLUMNS.type}>
        <span className="sr-only">Type </span>
        <span className="rounded-full border border-border-strong bg-surface px-[9px] py-[3px] text-2xs font-medium text-muted-foreground">
          {config.data_type}
        </span>
      </span>
      <span
        title={allowed}
        className={cn(COLUMNS.allowed, "basis-full truncate @[40rem]:basis-auto")}
      >
        <span className="sr-only">Allowed values: </span>
        {allowed}
      </span>
      <span className={cn(COLUMNS.origin, "text-xs text-muted-foreground")}>
        {config.implicit ? "Created from the first score" : "Defined here"}
      </span>
      {canWrite ? (
        <span className={cn(COLUMNS.actions, "ml-auto flex justify-end gap-1.5")}>
          <ScoreConfigDialog
            config={config}
            trigger={
              <Button
                variant="secondary"
                size="icon-sm"
                aria-label={`Edit ${config.name}`}
                className="shadow-none"
              >
                <Pencil aria-hidden />
              </Button>
            }
          />
          <DeleteScoreConfigDialog
            config={config}
            trigger={
              <Button
                variant="secondary"
                size="icon-sm"
                aria-label={`Delete ${config.name}`}
                className="shadow-none"
              >
                <Trash2 aria-hidden />
              </Button>
            }
          />
        </span>
      ) : null}
    </li>
  );
}
