import { Plus } from "lucide-react";
import { useRef, useState } from "react";

import { DisabledReason } from "@/components/disabled-reason";
import { ErrorState } from "@/components/error-state";
import { Notice } from "@/components/notice";
import { PageHeader } from "@/components/page-header";
import { ReadOnlyNote } from "@/components/read-only-note";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { usePermission } from "@/features/shell";
import { cn } from "@/lib/utils";

import { ConfigColumn } from "./config-column";
import {
  COLUMN_LETTERS,
  MAX_COLUMNS,
  addColumn,
  canAddColumn,
  duplicateColumn,
  newColumn,
  removeColumn,
  runHeadingId,
  updateColumn,
} from "./config-state";
import { useCredentialsQuery, usePricesQuery } from "./playground-queries";
import { IDLE } from "./run-state";
import { usePlaygroundRun } from "./use-playground-run";
import { useSeedColumn } from "./use-seed-column";

const DESCRIPTION =
  "Try a prompt version or free-form messages against up to three models. Every run is a real provider call.";

const GRID: Record<number, string> = {
  1: "grid-cols-1",
  2: "grid-cols-1 md:grid-cols-2",
  3: "grid-cols-1 md:grid-cols-2 xl:grid-cols-3",
};

/** Figma "Playground" (1, 2 and 3 columns, running, results, errors, 375 px). */
export function PlaygroundPage() {
  const canRun = usePermission("experiments:run");
  const credentialsQuery = useCredentialsQuery();
  const prices = usePricesQuery();
  const counter = useRef(1);
  const [columns, setColumns] = useState(() => [newColumn("col-0")]);
  const credentials = credentialsQuery.data ?? [];
  const { states, notice, running, run } = usePlaygroundRun(
    columns,
    credentials.map((credential) => credential.id),
  );
  const seedProblem = useSeedColumn(setColumns);
  const nextKey = () => `col-${counter.current++}`;

  function remove(key: string) {
    const index = columns.findIndex((column) => column.key === key);
    const neighbour = columns[index + 1] ?? columns[index - 1];
    setColumns((current) => removeColumn(current, key));
    // The remove button left with its column: move focus to the neighbouring run's heading.
    window.setTimeout(() => {
      if (neighbour) {
        document.getElementById(runHeadingId(neighbour.key))?.focus();
      }
    });
  }
  const many = columns.length > 1;

  if (!canRun) {
    return (
      <div className="flex flex-col gap-6">
        <PageHeader title="Playground" description={DESCRIPTION} />
        <ReadOnlyNote>
          Only members, admins and owners can run the playground, because every run is a real
          provider call that is paid for. You can still read the traces it produced in the
          playground environment.
        </ReadOnlyNote>
      </div>
    );
  }

  const addButton = (
    <Button
      variant="secondary"
      disabled={!canAddColumn(columns) || running}
      onClick={() => setColumns((current) => addColumn(current, nextKey()))}
    >
      <Plus aria-hidden />
      Add column
    </Button>
  );

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Playground"
        description={DESCRIPTION}
        actions={
          <>
            {canAddColumn(columns) ? (
              addButton
            ) : (
              <DisabledReason
                reason={`${MAX_COLUMNS} is the most the playground runs side by side.`}
              >
                {addButton}
              </DisabledReason>
            )}
            <Button variant="primary" loading={running} onClick={() => void run()}>
              {many ? "Run all" : "Run"}
            </Button>
          </>
        }
      />
      {seedProblem ? (
        <Notice tone="warning" role="alert">
          {seedProblem}
        </Notice>
      ) : null}
      {notice ? (
        <Notice tone="warning" role="alert">
          {notice}
        </Notice>
      ) : null}
      {credentialsQuery.isError ? (
        <ErrorState
          compact
          error={credentialsQuery.error}
          title="Couldn't load credentials"
          onRetry={() => void credentialsQuery.refetch()}
        />
      ) : null}
      {credentialsQuery.isSuccess && credentials.length === 0 ? (
        <Notice>
          This organization has no provider credentials yet. An owner adds one under Gateway,
          Credentials, and then it shows up here.
        </Notice>
      ) : null}
      {credentialsQuery.isPending ? (
        <Skeleton className="h-96 w-full rounded-card" />
      ) : (
        <div className={cn("grid items-start gap-5", GRID[columns.length])}>
          {columns.map((column, index) => (
            <ConfigColumn
              key={column.key}
              column={column}
              letter={COLUMN_LETTERS[index] ?? ""}
              state={states[column.key] ?? IDLE}
              credentials={credentials}
              prices={prices.data ?? []}
              canAdd={canAddColumn(columns)}
              canRemove={many}
              onChange={(patch) =>
                setColumns((current) => updateColumn(current, column.key, patch))
              }
              onRemove={() => remove(column.key)}
              onDuplicate={() =>
                setColumns((current) => duplicateColumn(current, column.key, nextKey()))
              }
            />
          ))}
        </div>
      )}
    </div>
  );
}
