import { Info, Plus, Wallet } from "lucide-react";
import type { ReactNode } from "react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { PageHeader } from "@/components/page-header";
import { ReadOnlyNote } from "@/components/read-only-note";
import { SectionCard } from "@/components/section-card";
import { TileListSkeleton } from "@/components/tile-list";
import { Button } from "@/components/ui/button";
import { usePermission, useProjectQuery } from "@/features/shell";
import { viewOnlyCopy } from "@/lib/permissions";

import { BudgetDialog } from "./budget-dialog";
import { softLimitNote } from "./budget-enforcement";
import { BudgetList } from "./budget-list";
import { useBudgetsQuery, useGatewayKeysQuery } from "./budgets-queries";

/**
 * Figma "Budgets — List" and its states (empty, loading, error, 375 px). Every member sees the
 * budgets and their spend; admins and owners create, edit and delete them.
 */
export function BudgetsPage() {
  const canWrite = usePermission("alerts:write");
  const query = useBudgetsQuery();

  const createButton = canWrite ? (
    <BudgetDialog
      budget={null}
      trigger={
        <Button variant="primary">
          <Plus aria-hidden />
          Create budget
        </Button>
      }
    />
  ) : null;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Budgets"
        description="Cap spend per project, gateway key, end user or model."
      />
      <SectionCard
        title="Budgets"
        description="Spend resets at the start of each period, in UTC."
        className="gap-4"
        actions={createButton}
      >
        {canWrite ? null : <ReadOnlyNote>{viewOnlyCopy("budgets", "alerts:write")}</ReadOnlyNote>}
        <BudgetsContent query={query} canWrite={canWrite} createButton={createButton} />
        <p className="flex items-center gap-2 px-1 pt-2 text-xs font-medium text-muted-foreground">
          <Info aria-hidden className="size-3.5 shrink-0" />
          {softLimitNote()}
        </p>
      </SectionCard>
    </div>
  );
}

interface BudgetsContentProps {
  query: ReturnType<typeof useBudgetsQuery>;
  canWrite: boolean;
  createButton: ReactNode;
}

function BudgetsContent({ query, canWrite, createButton }: BudgetsContentProps) {
  const project = useProjectQuery();
  const keys = useGatewayKeysQuery();

  if (query.isPending) {
    return <TileListSkeleton label="Loading budgets" rows={4} className="h-[58px]" />;
  }
  // A failed background refresh keeps the loaded list on screen; polling goes on.
  if (query.data === undefined) {
    return (
      <ErrorState
        compact
        error={query.error}
        title="Couldn't load budgets"
        onRetry={() => {
          void query.refetch();
        }}
      />
    );
  }
  if (query.data.length === 0) {
    return (
      <EmptyState
        icon={Wallet}
        title="No budgets"
        description={`Set spend limits per project, gateway key, end user, model, metadata or trace name. ${softLimitNote()}`}
        action={createButton ?? undefined}
        className="rounded-tile bg-surface-muted"
      />
    );
  }
  return (
    <BudgetList
      budgets={query.data}
      gatewayKeys={keys.data}
      projectName={project.data?.name ?? null}
      canWrite={canWrite}
    />
  );
}
