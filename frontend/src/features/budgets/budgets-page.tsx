import { Info, Plus, Wallet } from "lucide-react";
import { useId, type ReactNode } from "react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { PageHeader } from "@/components/page-header";
import { SectionCard } from "@/components/section-card";
import { TileListSkeleton } from "@/components/tile-list";
import { Button } from "@/components/ui/button";
import { AlertsReadOnlyLine } from "@/features/alerts";
import { usePermission, useProjectQuery } from "@/features/shell";

import { BudgetDialog } from "./budget-dialog";
import { BudgetList } from "./budget-list";
import { useBudgetsQuery, useGatewayKeysQuery } from "./budgets-queries";

/**
 * Figma "Budgets — List" and its states (empty, loading, error, 375 px). Every member sees the
 * budgets and their spend; admins and owners create, edit and delete them.
 */
export function BudgetsPage() {
  const canWrite = usePermission("alerts:write");
  const readOnlyId = useId();
  const query = useBudgetsQuery();

  const createButton = (
    <BudgetDialog
      budget={null}
      trigger={
        <Button
          variant="primary"
          disabled={!canWrite}
          aria-describedby={canWrite ? undefined : readOnlyId}
        >
          <Plus aria-hidden />
          Create budget
        </Button>
      }
    />
  );

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
        actions={
          <div className="flex flex-wrap items-center justify-end gap-3">
            {canWrite ? null : (
              <div id={readOnlyId}>
                <AlertsReadOnlyLine />
              </div>
            )}
            {createButton}
          </div>
        }
      >
        <BudgetsContent
          query={query}
          canWrite={canWrite}
          readOnlyId={readOnlyId}
          createButton={createButton}
        />
        <p className="flex items-center gap-2 px-1 pt-2 text-xs font-medium text-muted-foreground">
          <Info aria-hidden className="size-3.5 shrink-0" />
          Blocking budgets refuse gateway calls once an evaluation (every minute) finds them spent.
          Traces sent with the SDK are never blocked.
        </p>
      </SectionCard>
    </div>
  );
}

interface BudgetsContentProps {
  query: ReturnType<typeof useBudgetsQuery>;
  canWrite: boolean;
  readOnlyId: string;
  createButton: ReactNode;
}

function BudgetsContent({ query, canWrite, readOnlyId, createButton }: BudgetsContentProps) {
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
        description="Set spend limits per project, gateway key, end user or model. Blocking budgets refuse gateway calls once an evaluation (every minute) finds them spent."
        action={canWrite ? createButton : undefined}
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
      readOnlyId={readOnlyId}
    />
  );
}
