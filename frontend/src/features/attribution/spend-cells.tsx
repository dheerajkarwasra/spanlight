import { Link } from "@tanstack/react-router";

import { CopyButton } from "@/components/copy-button";
import { UnknownValue } from "@/components/unknown-value";
import { useProjectParams } from "@/features/shell/project-context";
import { formatInteger } from "@/lib/format";

import { NoneLabel, OtherLabel } from "./attribution-footer";
import { NO_GROUP_PRICE, REDACTED } from "./attribution-grid";
import { DrillLink, ShareCell, VALUE_LINK_CLASS, ValueLabel } from "./attribution-row";
import { drillDownSearch, type DrillContext } from "./drill-down";
import { RowActions } from "./row-actions";
import type { SpendRow } from "./spend-rows";

interface GroupCellProps {
  row: SpendRow;
  drill: DrillContext;
  onFocus: (value: string) => void;
}

/**
 * The Group cell: a value links to its traces and carries its row menu at the right edge, so the
 * menu stays in the sticky column; none, other and the total keep their copy.
 */
export function GroupCell({ row, drill, onFocus }: GroupCellProps) {
  switch (row.kind) {
    case "value": {
      const search = drillDownSearch(drill, row.value);
      const label = <ValueLabel value={row.value} />;
      return (
        <span className="flex min-w-0 items-center gap-1.5">
          {search ? (
            <DrillLink search={search} title={row.value} className={VALUE_LINK_CLASS}>
              {label}
            </DrillLink>
          ) : (
            label
          )}
          {row.value === REDACTED ? null : (
            <CopyButton
              value={row.value}
              label="Copy value"
              className="shrink-0 opacity-0 group-hover/row:opacity-100 focus-visible:opacity-100"
            />
          )}
          <RowActions value={row.value} drill={drill} onFocus={onFocus} className="ml-auto" />
        </span>
      );
    }
    case "none":
      return <NoneLabel drill={drill} linked />;
    case "other":
      return <OtherLabel other={row.bucket} />;
    case "total":
      return "Total";
  }
}

/** The count, and a link to Pricing when calls had no price (spec ruling 9). */
export function UnpricedCell({ count }: { count: number }) {
  const { orgId, projectId } = useProjectParams();
  return (
    <span className="inline-flex items-center gap-2">
      {formatInteger(count)}
      {count > 0 ? (
        <Link
          to="/$orgId/$projectId/pricing"
          params={{ orgId, projectId }}
          className="inline-flex min-h-6 items-center rounded-sm font-sans text-accent hover:underline"
        >
          Set prices
        </Link>
      ) : null}
    </span>
  );
}

export function ShareOrWhole({ row }: { row: SpendRow }) {
  if (row.share !== "total") {
    return <ShareCell share={row.share} track="w-12" />;
  }
  return row.figures.cost_usd === null ? (
    <UnknownValue reason={NO_GROUP_PRICE} />
  ) : (
    <span className="text-muted-foreground">100%</span>
  );
}
