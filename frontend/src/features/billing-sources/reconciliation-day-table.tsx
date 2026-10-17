import { Info } from "lucide-react";
import { useCallback, useId, useRef, useState, type ReactNode, type RefCallback } from "react";

import { SectionCard } from "@/components/section-card";
import { Skeleton } from "@/components/ui/skeleton";

import { DayTableRow, DayTile } from "./reconciliation-day-row";
import { VarianceLegend } from "./reconciliation-figure";
import type { DayRow } from "./reconciliation-table";

/** The eight columns need about 700 px; in a narrower card each day is a tile (Figma "mobile 375"). */
const TABLE_MIN_WIDTH = 700;

const TAP_HINT = "Tap a day for its model groups.";

const DESCRIPTION =
  "Residual = provider − Spanlight − explained, signed. Variance = residual ÷ provider. Days are UTC; credits and other provider charges are listed apart inside each day and never subtracted.";

const COLUMNS = ["Day", "Status", "Provider", "Spanlight", "Explained", "Residual", "Variance"];

interface DailyCardProps {
  /** The legend is left out of the error state (Figma "Reconciliation / error"). */
  legend?: boolean;
  footnote?: string | null;
  children: ReactNode;
}

/** Figma "Day table card": title, formula, variance legend, then the days and a footnote. */
export function DailyCard({ legend = true, footnote = null, children }: DailyCardProps) {
  return (
    <SectionCard
      title="Daily reconciliation"
      description={DESCRIPTION}
      actions={legend ? <VarianceLegend /> : null}
      className="gap-3"
    >
      {children}
      {footnote ? (
        <p className="flex gap-2 px-1 pt-1.5 text-xs font-medium text-muted-foreground">
          <Info aria-hidden className="mt-0.5 size-3.5 shrink-0" />
          <span>{footnote}</span>
        </p>
      ) : null}
    </SectionCard>
  );
}

function useExpanded(): [ReadonlySet<string>, (day: string) => void] {
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  const toggle = (day: string) => {
    setExpanded((current) => {
      const next = new Set(current);
      if (!next.delete(day)) {
        next.add(day);
      }
      return next;
    });
  };
  return [expanded, toggle];
}

/**
 * Whether the element is wide enough for the table, measured on the element itself (a container
 * query in JS), so the table shows whenever it fits, rail or not. Measured once before paint, then
 * on every resize; without ResizeObserver (tests) the tiles are used.
 */
function useFitsTable(): [RefCallback<HTMLDivElement>, boolean] {
  const [wide, setWide] = useState(false);
  const observer = useRef<ResizeObserver | null>(null);
  const ref = useCallback((node: HTMLDivElement | null) => {
    observer.current?.disconnect();
    observer.current = null;
    if (node === null || typeof ResizeObserver === "undefined") {
      return;
    }
    setWide(node.getBoundingClientRect().width >= TABLE_MIN_WIDTH);
    observer.current = new ResizeObserver(([entry]) => {
      setWide((entry?.contentRect.width ?? 0) >= TABLE_MIN_WIDTH);
    });
    observer.current.observe(node);
  }, []);
  return [ref, wide];
}

/** The days, newest first: a real table when the card is wide enough, disclosure tiles if not. */
export function DayTable({ rows }: { rows: DayRow[] }) {
  const [measure, wide] = useFitsTable();
  return (
    <div ref={measure}>
      <DayTableContent rows={rows} wide={wide} />
    </div>
  );
}

function DayTableContent({ rows, wide }: { rows: DayRow[]; wide: boolean }) {
  const [expanded, toggle] = useExpanded();
  const idPrefix = useId();
  const props = (row: DayRow) => ({
    row,
    expanded: expanded.has(row.day),
    onToggle: () => {
      toggle(row.day);
    },
    detailId: `${idPrefix}-${row.day}`,
  });

  if (!wide) {
    return (
      <div className="flex flex-col gap-2">
        <p className="text-xs font-medium text-muted-foreground">{TAP_HINT}</p>
        <ul aria-label="Daily reconciliation" className="flex flex-col gap-2">
          {rows.map((row) => (
            <DayTile key={row.day} {...props(row)} />
          ))}
        </ul>
      </div>
    );
  }
  return (
    <table className="w-full">
      <caption className="sr-only">Daily reconciliation, newest day first</caption>
      <thead>
        <tr className="text-2xs font-semibold tracking-[0.06em] text-subtle-foreground uppercase">
          {COLUMNS.map((column, index) => (
            <th
              key={column}
              scope="col"
              className={index < 2 ? "px-2 pb-1 text-left first:pl-3" : "px-2 pb-1 text-right"}
            >
              {column}
            </th>
          ))}
          <th scope="col" className="w-10 pb-1">
            <span className="sr-only">Model groups</span>
          </th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <DayTableRow key={row.day} {...props(row)} />
        ))}
      </tbody>
    </table>
  );
}

/** Figma "Reconciliation / loading": shimmering rows and what is being loaded. */
export function DayTableSkeleton({ label }: { label: string }) {
  return (
    <div role="status" aria-live="polite" className="flex flex-col gap-1.5">
      {Array.from({ length: 7 }, (_, index) => (
        <div
          key={index}
          aria-hidden
          className="flex h-[60px] items-center gap-4 rounded-tile bg-surface-muted px-4"
        >
          <Skeleton className="h-3 w-24 bg-surface-hover" />
          <Skeleton className="hidden h-3 flex-1 bg-surface-hover sm:block" />
          <Skeleton className="h-6 w-16 rounded-full bg-surface-hover" />
        </div>
      ))}
      <p className="pt-2 text-xs font-medium text-muted-foreground">{label}</p>
    </div>
  );
}
