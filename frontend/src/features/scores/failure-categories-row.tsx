import { ListChecks } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { ScoreConfig } from "@/lib/api";

import { FailureCategoriesDialog } from "./failure-categories-dialog";
import { FAILURE_CATEGORY_SCORE } from "./failure-category";

interface FailureCategoriesRowProps {
  configs: ScoreConfig[];
  canWrite: boolean;
}

/**
 * Settings › Scores "Failure categories" row: the categories reviewers label traces with and
 * "Edit", or "Set up" while the project has no `failure_category` config.
 */
export function FailureCategoriesRow({ configs, canWrite }: FailureCategoriesRowProps) {
  const config = configs.find((candidate) => candidate.name === FAILURE_CATEGORY_SCORE);
  const list = config?.categories.join(" · ") ?? "";
  return (
    <div
      id="failure-categories"
      className="flex scroll-mt-6 flex-wrap items-center gap-x-3 gap-y-2 rounded-tile border border-border py-2.5 pr-3 pl-4 text-sm"
    >
      <ListChecks aria-hidden className="size-4 shrink-0 text-muted-foreground" />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="font-semibold text-foreground">Failure categories</span>
        {config ? (
          <span title={list} className="truncate font-mono text-xs text-muted-foreground">
            <span className="sr-only">Categories: </span>
            {list === "" ? "Any value" : list}
          </span>
        ) : (
          <span className="text-xs text-muted-foreground">
            Not set up. Reviewers label each trace with one, and Releases shows each one&apos;s
            rate.
          </span>
        )}
      </div>
      {canWrite ? (
        <FailureCategoriesDialog
          config={config}
          trigger={
            <Button
              variant="secondary"
              size="sm"
              aria-label={config ? "Edit failure categories" : "Set up failure categories"}
              className="ml-auto max-md:min-h-11"
            >
              {config ? "Edit" : "Set up"}
            </Button>
          }
        />
      ) : null}
    </div>
  );
}
