import type { ChargeRow, DayRow } from "./reconciliation-table";
import { ReconciliationGroup } from "./reconciliation-group";

interface ChargesCardProps {
  title: string;
  lead: string;
  rows: ChargeRow[];
}

function ChargesCard({ title, lead, rows }: ChargesCardProps) {
  return (
    <section className="flex min-w-0 flex-col gap-1.5 rounded-input border border-border bg-surface px-4 py-3.5">
      <h4 className="text-overline text-subtle-foreground uppercase">{title}</h4>
      <p className="text-xs font-medium text-muted-foreground">{lead}</p>
      {rows.length === 0 ? (
        <p className="text-label text-subtle-foreground">None on this day.</p>
      ) : (
        <ul className="flex flex-col gap-1">
          {rows.map((row, index) => (
            <li
              key={`${row.description}-${String(index)}`}
              className="flex gap-3 text-label text-foreground"
            >
              <span className="min-w-0 flex-1 font-semibold [overflow-wrap:anywhere]">
                {row.description}
              </span>
              <span className="shrink-0 font-mono">{row.usd}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/**
 * Figma "Day detail": when the day was fetched and how many model groups it has, each group, the
 * credits and the other provider charges (listed apart, never in the residual), and a footnote.
 */
export function ReconciliationDayDetail({ row }: { row: DayRow }) {
  const charges = row.chargesKnown && (row.credits.length > 0 || row.other.length > 0);
  return (
    <div className="@container flex flex-col gap-3">
      <p className="text-xs font-medium text-muted-foreground">{row.meta}</p>
      {row.groups.map((group) => (
        <ReconciliationGroup key={group.key} group={group} />
      ))}
      {charges ? (
        <div className="grid gap-3 @[36rem]:grid-cols-2">
          <ChargesCard
            title="Credits"
            lead="Listed apart, never subtracted from the residual."
            rows={row.credits}
          />
          <ChargesCard
            title="Other provider charges"
            lead="Charges Spanlight cannot see in spans."
            rows={row.other}
          />
        </div>
      ) : null}
      {row.footnote ? (
        <p className="text-xs font-medium text-subtle-foreground">{row.footnote}</p>
      ) : null}
    </div>
  );
}
