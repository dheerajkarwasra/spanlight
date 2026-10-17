import { ChevronDown } from "lucide-react";
import type { MouseEvent } from "react";

import { Badge } from "@/components/ui/badge";
import { Tooltip } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

import { ReconciliationDayDetail } from "./reconciliation-day-detail";
import { FigureText, VarianceCell } from "./reconciliation-figure";
import type { DayChip, DayRow, Figure } from "./reconciliation-table";

interface DayRowProps {
  row: DayRow;
  expanded: boolean;
  onToggle: () => void;
  /** The id of the expanded detail, for `aria-controls`. */
  detailId: string;
}

const NUMBER_CELL = "px-2 py-3 text-right font-mono text-label whitespace-nowrap text-foreground";

/** The day's chips; plain inside the mobile tile's button, where a tooltip can't be focused. */
function DayChips({ chips, plain = false }: { chips: DayChip[]; plain?: boolean }) {
  return chips.map((chip) =>
    plain ? (
      <Badge key={chip.label}>
        {chip.label}
        <span className="sr-only">, </span>
      </Badge>
    ) : (
      <Tooltip key={chip.label} content={chip.tooltip} caret="start" side="bottom">
        <Badge tabIndex={0} className="cursor-help">
          {chip.label}
        </Badge>
      </Tooltip>
    ),
  );
}

function Chevron({ expanded }: { expanded: boolean }) {
  return (
    <ChevronDown
      aria-hidden
      className={cn(
        "size-4 transition-transform duration-200 motion-reduce:transition-none",
        expanded && "rotate-180",
      )}
    />
  );
}

/**
 * Figma "Bet1/Day row" in the daily table: day, chips, the four figures, the variance badge and the
 * disclosure button that opens the day's model groups in a row of its own. The whole row toggles on
 * click; the keyboard path is the button.
 */
export function DayTableRow({ row, expanded, onToggle, detailId }: DayRowProps) {
  function toggleFromButton(event: MouseEvent) {
    event.stopPropagation();
    onToggle();
  }
  const figures: Figure[] = [row.provider, row.spanlight, row.explained, row.residual];
  return (
    <>
      <tr
        onClick={onToggle}
        className={cn(
          "cursor-pointer border-b border-border transition-colors hover:bg-surface-hover",
          expanded && "border-transparent bg-surface-muted hover:bg-surface-muted",
        )}
      >
        <th
          scope="row"
          className="py-3 pr-2 pl-3 text-left font-mono text-label font-normal whitespace-nowrap text-foreground"
        >
          {row.label}
        </th>
        <td className="px-2 py-3">
          <div className="flex flex-wrap gap-1">
            <DayChips chips={row.chips} />
          </div>
        </td>
        {figures.map((figure, index) => (
          <td key={index} className={NUMBER_CELL}>
            <FigureText figure={figure} />
          </td>
        ))}
        <td className="px-2 py-3 text-right">
          <VarianceCell figure={row.variance} />
        </td>
        <td className="py-2 pr-2 pl-1 text-right">
          <button
            type="button"
            aria-expanded={expanded}
            aria-controls={detailId}
            aria-label={`${expanded ? "Hide" : "Show"} model groups for ${row.label}`}
            onClick={toggleFromButton}
            className="inline-flex size-8 items-center justify-center rounded-full text-foreground hover:bg-surface-hover"
          >
            <Chevron expanded={expanded} />
          </button>
        </td>
      </tr>
      {expanded ? (
        <tr id={detailId} className="border-b border-border">
          <td colSpan={8} className="bg-surface-muted px-3 pt-1 pb-4">
            <ReconciliationDayDetail row={row} />
          </td>
        </tr>
      ) : null}
    </>
  );
}

function TileFigure({ label, figure }: { label: string; figure: Figure }) {
  return (
    <span className="flex min-w-0 flex-col gap-px">
      <span className="text-2xs font-medium text-subtle-foreground">
        {label}
        <span className="sr-only">: </span>
      </span>
      <FigureText
        plain
        figure={figure}
        className="font-mono text-label [overflow-wrap:anywhere] text-foreground"
      />
      <span className="sr-only">. </span>
    </span>
  );
}

/**
 * Figma "Bet1/Day tile (mobile)": the same day as one disclosure button (day, chips, variance and
 * the four figures), with the detail opening under it.
 */
export function DayTile({ row, expanded, onToggle, detailId }: DayRowProps) {
  return (
    <li className="rounded-tile bg-surface-muted">
      <button
        type="button"
        aria-expanded={expanded}
        aria-controls={detailId}
        onClick={onToggle}
        className="flex w-full flex-col gap-2 rounded-tile p-3.5 text-left"
      >
        <span className="flex min-h-11 items-center gap-2">
          <span className="font-mono text-label text-foreground">
            {row.label}
            <span className="sr-only">. </span>
          </span>
          <span className="flex flex-1 flex-wrap justify-end gap-1">
            <DayChips plain chips={row.chips} />
            {row.chips.length > 0 ? <span className="sr-only">. </span> : null}
          </span>
          <VarianceCell plain figure={row.variance} />
          <span className="sr-only">. </span>
          <Chevron expanded={expanded} />
        </span>
        <span className="grid grid-cols-4 gap-2">
          <TileFigure label="Provider" figure={row.provider} />
          <TileFigure label="Spanlight" figure={row.spanlight} />
          <TileFigure label="Explained" figure={row.explained} />
          <TileFigure label="Residual" figure={row.residual} />
        </span>
        {row.notes.length > 0 ? (
          <span className="flex flex-col gap-0.5 text-xs font-medium text-muted-foreground">
            {row.notes.map((note) => (
              <span key={note}>{note}</span>
            ))}
          </span>
        ) : null}
      </button>
      {expanded ? (
        <div id={detailId} className="px-3 pb-3">
          <ReconciliationDayDetail row={row} />
        </div>
      ) : null}
    </li>
  );
}
