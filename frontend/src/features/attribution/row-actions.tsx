import { Link } from "@tanstack/react-router";
import { ArrowUpRight, CalendarClock, Ellipsis, SlidersHorizontal } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { usePermission, useProjectParams } from "@/features/shell/project-context";
import { cn } from "@/lib/utils";

import { budgetSearchFor } from "./budget-link";
import { drillDownSearch, type DrillContext } from "./drill-down";

interface RowActionsProps {
  value: string;
  drill: DrillContext;
  /** Charts this group (`focus` in the URL). */
  onFocus: (value: string) => void;
  /** Phones get a 44 px trigger; the table row keeps 32 px. */
  className?: string;
}

/** Figma "Spend — row menu": Show over time, Set a budget for this (admins), Open traces. */
export function RowActions({ value, drill, onFocus, className }: RowActionsProps) {
  const { orgId, projectId } = useProjectParams();
  const canBudget = usePermission("alerts:write");
  const budget = canBudget ? budgetSearchFor(drill.groupBy, drill.key, value) : null;
  const traces = drillDownSearch(drill, value);

  return (
    <DropdownMenu modal={false}>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={`Actions for ${value}`}
          className={cn("shrink-0", className)}
        >
          <Ellipsis aria-hidden />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-52">
        <DropdownMenuItem
          onSelect={() => {
            onFocus(value);
          }}
        >
          <CalendarClock aria-hidden />
          Show over time
        </DropdownMenuItem>
        {budget ? (
          <DropdownMenuItem asChild>
            <Link to="/$orgId/$projectId/budgets" params={{ orgId, projectId }} search={budget}>
              <SlidersHorizontal aria-hidden />
              Set a budget for this
            </Link>
          </DropdownMenuItem>
        ) : null}
        {traces ? (
          <DropdownMenuItem asChild>
            <Link to="/$orgId/$projectId/traces" params={{ orgId, projectId }} search={traces}>
              <ArrowUpRight aria-hidden />
              Open traces
            </Link>
          </DropdownMenuItem>
        ) : null}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
