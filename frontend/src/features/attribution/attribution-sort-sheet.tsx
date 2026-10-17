import { ArrowUpDown, Check } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import type { AttributionSort } from "@/lib/api";
import { cn } from "@/lib/utils";

import { SORT_COLUMNS, sortLabel } from "./attribution-sort";

interface AttributionSortSheetProps {
  sort: AttributionSort;
  onSort: (sort: AttributionSort) => void;
}

/** Figma "Attribution / Page (375)" sort sheet: the phone's way to pick the sort column. */
export function AttributionSortSheet({ sort, onSort }: AttributionSortSheetProps) {
  const [open, setOpen] = useState(false);
  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        {/* 40 px button, 44 px hit area on phones (RULING T20-4). */}
        <Button className="relative w-full before:absolute before:inset-x-0 before:-inset-y-0.5">
          <ArrowUpDown aria-hidden />
          {`Sort: ${sortLabel(sort)}`}
        </Button>
      </SheetTrigger>
      <SheetContent className="inset-x-2 top-auto bottom-2 w-auto max-w-none animate-fade-up gap-1 p-4 pt-5">
        <SheetTitle className="pb-3 text-base font-semibold">Sort by</SheetTitle>
        <ul className="flex flex-col gap-1">
          {SORT_COLUMNS.map((column) => {
            const active = column.sort === sort;
            return (
              <li key={column.sort}>
                <button
                  type="button"
                  aria-pressed={active}
                  onClick={() => {
                    onSort(column.sort);
                    setOpen(false);
                  }}
                  className={cn(
                    "flex h-12 w-full items-center justify-between rounded-input px-3.5 text-sm text-foreground",
                    active ? "bg-accent-subtle font-semibold" : "hover:bg-surface-hover",
                  )}
                >
                  {column.label}
                  {active ? <Check aria-hidden className="size-4 text-accent" /> : null}
                </button>
              </li>
            );
          })}
        </ul>
        <SheetDescription className="pt-2 text-xs text-muted-foreground">
          Highest first. Ties sort by value, A to Z.
        </SheetDescription>
      </SheetContent>
    </Sheet>
  );
}
