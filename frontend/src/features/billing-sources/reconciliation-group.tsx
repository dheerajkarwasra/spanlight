import { cn } from "@/lib/utils";

import { FigureText, VarianceCell } from "./reconciliation-figure";
import type { ExplainedRow, Figure, GroupRow, TokenRow } from "./reconciliation-table";

const OVERLINE = "text-overline text-subtle-foreground uppercase";
const MONO = "font-mono text-label text-foreground";

function GroupFigure({ label, figure }: { label: string; figure: Figure }) {
  return (
    <div className="flex min-w-0 flex-col gap-px @[40rem]:items-end">
      <dt className="text-2xs font-medium text-subtle-foreground">{label}</dt>
      <dd className={cn(MONO, "whitespace-nowrap")}>
        <FigureText figure={figure} />
      </dd>
    </div>
  );
}

function GroupHeader({ group }: { group: GroupRow }) {
  const variance = <VarianceCell figure={group.variance} />;
  return (
    <div className="flex flex-col gap-3 @[40rem]:flex-row @[40rem]:items-center @[40rem]:gap-4">
      <div className="flex min-w-0 flex-1 items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-0.5">
          <h4 className={cn(MONO, "[overflow-wrap:anywhere]")}>{group.name}</h4>
          {group.models.map((line) => (
            <p
              key={line}
              className="text-xs font-medium [overflow-wrap:anywhere] text-muted-foreground"
            >
              {line}
            </p>
          ))}
          {group.note ? (
            <p className="text-xs font-medium text-warning first-letter:uppercase">{group.note}</p>
          ) : null}
        </div>
        <span className="shrink-0 @[40rem]:hidden">{variance}</span>
      </div>
      <dl className="grid grid-cols-4 gap-2 @[40rem]:flex @[40rem]:items-center @[40rem]:gap-4">
        <GroupFigure label="Provider" figure={group.provider} />
        <GroupFigure label="Spanlight" figure={group.spanlight} />
        <GroupFigure label="Explained" figure={group.explained} />
        <GroupFigure label="Residual" figure={group.residual} />
      </dl>
      <span className="hidden shrink-0 @[40rem]:inline-flex">{variance}</span>
    </div>
  );
}

function TokenCell({ figure, muted = false }: { figure: Figure; muted?: boolean }) {
  return <FigureText figure={figure} className={muted ? "text-subtle-foreground" : undefined} />;
}

function TokenTableRow({ row }: { row: TokenRow }) {
  const sameCount = row.difference.text === "0";
  return (
    <tr className="border-b border-border last:border-0 @[36rem]:border-0">
      <th
        scope="row"
        className="py-1.5 pr-3 pl-1 text-left align-top font-medium text-muted-foreground"
      >
        {row.label}
        <span className="mt-0.5 block font-mono text-foreground @[36rem]:hidden">
          <TokenCell figure={row.provider} /> / <TokenCell figure={row.spanlight} />
        </span>
      </th>
      <td className="hidden py-1.5 pl-3 text-right font-mono text-foreground @[36rem]:table-cell">
        <TokenCell figure={row.provider} />
      </td>
      <td className="hidden py-1.5 pl-3 text-right font-mono text-foreground @[36rem]:table-cell">
        <TokenCell figure={row.spanlight} />
      </td>
      <td className="py-1.5 pr-1 pl-3 text-right align-top font-mono text-foreground">
        <TokenCell figure={row.difference} muted={sameCount} />
      </td>
    </tr>
  );
}

function TokenTable({ group }: { group: GroupRow }) {
  const head = "pb-0.5 font-semibold tracking-[0.06em] uppercase";
  return (
    <table className="w-full text-xs">
      <caption className="sr-only">
        Tokens by class for {group.name}: provider, Spanlight and the difference
      </caption>
      <thead className="text-2xs text-subtle-foreground">
        <tr>
          <th scope="col" className={cn(head, "pl-1 text-left")}>
            <span className="@[36rem]:hidden">Tokens · provider / Spanlight</span>
            <span className="hidden @[36rem]:inline">Token class</span>
          </th>
          <th scope="col" className={cn(head, "hidden w-[120px] text-right @[36rem]:table-cell")}>
            Provider
          </th>
          <th scope="col" className={cn(head, "hidden w-[120px] text-right @[36rem]:table-cell")}>
            Spanlight
          </th>
          <th scope="col" className={cn(head, "pr-1 text-right @[36rem]:w-[120px]")}>
            Difference
          </th>
        </tr>
      </thead>
      <tbody>
        {group.tokens.map((row) => (
          <TokenTableRow key={row.label} row={row} />
        ))}
      </tbody>
    </table>
  );
}

function ExplainedLineItem({ line }: { line: ExplainedRow }) {
  return (
    <li className="grid grid-cols-[1fr_auto] gap-x-3 gap-y-0.5 rounded-input bg-surface-muted px-3 py-2 @[36rem]:grid-cols-[168px_1fr_80px]">
      <p className="col-start-1 row-start-1 text-label font-semibold text-foreground">
        {line.label}
      </p>
      <p className="col-span-2 col-start-1 row-start-2 text-xs font-medium text-muted-foreground @[36rem]:col-span-1 @[36rem]:col-start-2 @[36rem]:row-start-1 @[36rem]:self-center">
        {line.note}
      </p>
      <p
        className={cn(
          MONO,
          "col-start-2 row-start-1 text-right whitespace-nowrap @[36rem]:col-start-3",
        )}
      >
        <FigureText figure={line.usd} />
      </p>
    </li>
  );
}

/**
 * Figma "Model group" (day expanded): the group's figures and variance, the token classes on both
 * sides with their difference, and the explained lines, each with the server's note.
 */
export function ReconciliationGroup({ group }: { group: GroupRow }) {
  return (
    <section className="@container flex flex-col gap-3.5 rounded-input border border-border bg-surface p-4">
      <GroupHeader group={group} />
      <TokenTable group={group} />
      {group.lines.length > 0 ? (
        <div className="flex flex-col gap-1.5">
          <h5 className={OVERLINE}>Explained</h5>
          <ul className="flex flex-col gap-1.5">
            {group.lines.map((line) => (
              <ExplainedLineItem key={line.key} line={line} />
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
