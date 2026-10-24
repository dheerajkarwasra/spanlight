import { NO_PRICE_REASON } from "@/components/cost-value";
import { ValueOrUnknown } from "@/components/unknown-value";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { GatewayKeyUsage } from "@/lib/api";
import { formatCost, formatInteger } from "@/lib/format";

import { EnvironmentBadge } from "./environment-badge";
import { ErrorCount, OverviewCard, QuietWindowNote } from "./overview-card";

const CARD = {
  title: "By key",
  description: "Requests and cost per gateway key",
  link: { to: "/$orgId/$projectId/gateway/keys", label: "All keys" },
} as const;

/** Requests, errors and cost per gateway key. A cost of "—" means a model had no price. */
export function KeyTable({ keys }: { keys: readonly GatewayKeyUsage[] }) {
  return (
    <OverviewCard {...CARD}>
      {keys.length === 0 ? (
        <QuietWindowNote />
      ) : (
        <>
          <div className="hidden md:block">
            <Table aria-label="Requests and cost by key">
              <TableHeader>
                <TableRow className="[&>th]:bg-surface-muted">
                  <TableHead className="rounded-l-2xl">Key</TableHead>
                  <TableHead>Environment</TableHead>
                  <TableHead className="text-right">Requests</TableHead>
                  <TableHead className="text-right">Errors</TableHead>
                  <TableHead className="rounded-r-2xl text-right">Cost</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {keys.map((key) => (
                  <TableRow key={key.key_id}>
                    <TableCell className="font-semibold text-foreground">{key.name}</TableCell>
                    <TableCell>
                      <EnvironmentBadge environment={key.environment} size="md" />
                    </TableCell>
                    <TableCell className="text-right tabular">
                      {formatInteger(key.requests)}
                    </TableCell>
                    <TableCell className="text-right">
                      <ErrorCount errors={key.errors} requests={key.requests} />
                    </TableCell>
                    <TableCell className="text-right tabular">
                      <ValueOrUnknown value={formatCost(key.cost_usd)} reason={NO_PRICE_REASON} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <ul className="flex flex-col gap-1.5 px-4 md:hidden">
            {keys.map((key) => (
              <li key={key.key_id} className="flex flex-col gap-1 rounded-2xl bg-surface-muted p-4">
                <div className="flex items-center justify-between gap-3">
                  <span className="min-w-0 truncate font-semibold text-foreground">{key.name}</span>
                  <EnvironmentBadge environment={key.environment} size="md" />
                </div>
                <p className="flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground tabular">
                  <span>{formatInteger(key.requests)} req</span>
                  <span aria-hidden>·</span>
                  <span>
                    <ErrorCount errors={key.errors} requests={key.requests} /> errors
                  </span>
                  <span aria-hidden>·</span>
                  <ValueOrUnknown value={formatCost(key.cost_usd)} reason={NO_PRICE_REASON} />
                </p>
              </li>
            ))}
          </ul>
        </>
      )}
    </OverviewCard>
  );
}

export function KeyTableSkeleton() {
  return (
    <OverviewCard {...CARD}>
      <div aria-hidden className="flex flex-col gap-3 px-6 pb-4">
        {[0, 1, 2, 3].map((row) => (
          <Skeleton key={row} className="h-10 w-full" />
        ))}
      </div>
    </OverviewCard>
  );
}
