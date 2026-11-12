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
import type { GatewayTargetUsage } from "@/lib/api";
import { formatInteger } from "@/lib/format";

import { ErrorCount, OverviewCard, QuietWindowNote } from "./overview-card";
import { formatMs, providerLabel } from "./overview-format";

const CARD = {
  title: "By target",
  description: "Requests per route target in this window · a target is a provider credential",
  link: { to: "/$orgId/$projectId/gateway/routes", label: "Edit routes" },
} as const;

const NO_PROVIDER = "The credential was deleted.";
const NO_P95 = "No latency recorded for this target.";

/** Requests, errors and p95 per provider credential the routes sent calls to. */
export function TargetTable({ targets }: { targets: readonly GatewayTargetUsage[] }) {
  return (
    <OverviewCard {...CARD}>
      {targets.length === 0 ? (
        <QuietWindowNote />
      ) : (
        <>
          <div className="hidden md:block">
            <Table aria-label="Requests by target">
              <TableHeader>
                <TableRow className="[&>th]:bg-surface-muted">
                  <TableHead className="rounded-l-2xl">Target</TableHead>
                  <TableHead>Provider</TableHead>
                  <TableHead className="text-right">Requests</TableHead>
                  <TableHead className="text-right">Errors</TableHead>
                  <TableHead className="rounded-r-2xl text-right">p95</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {targets.map((target, index) => (
                  <TableRow key={target.credential_id ?? `deleted-${index}`}>
                    <TableCell className="font-semibold text-foreground">
                      <span title={target.credential_name} className="block max-w-[16rem] truncate">
                        {target.credential_name}
                      </span>
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      <ValueOrUnknown value={providerLabel(target.provider)} reason={NO_PROVIDER} />
                    </TableCell>
                    <TableCell className="text-right tabular">
                      {formatInteger(target.requests)}
                    </TableCell>
                    <TableCell className="text-right">
                      <ErrorCount errors={target.errors} requests={target.requests} />
                    </TableCell>
                    <TableCell className="text-right tabular">
                      <ValueOrUnknown value={formatMs(target.p95_ms)} reason={NO_P95} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <ul className="flex flex-col gap-1.5 px-4 md:hidden">
            {targets.map((target, index) => (
              <li
                key={target.credential_id ?? `deleted-${index}`}
                className="flex flex-col gap-1 rounded-2xl bg-surface-muted p-4"
              >
                <div className="flex items-baseline justify-between gap-3">
                  <span className="min-w-0 truncate font-semibold text-foreground">
                    {target.credential_name}
                  </span>
                  <span className="shrink-0 text-xs text-muted-foreground">
                    <ValueOrUnknown value={providerLabel(target.provider)} reason={NO_PROVIDER} />
                  </span>
                </div>
                <p className="flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground tabular">
                  <span>{formatInteger(target.requests)} req</span>
                  <span aria-hidden>·</span>
                  <span>
                    <ErrorCount errors={target.errors} requests={target.requests} /> errors
                  </span>
                  <span aria-hidden>·</span>
                  <span>
                    p95 <ValueOrUnknown value={formatMs(target.p95_ms)} reason={NO_P95} />
                  </span>
                </p>
              </li>
            ))}
          </ul>
        </>
      )}
    </OverviewCard>
  );
}

export function TargetTableSkeleton() {
  return (
    <OverviewCard {...CARD}>
      <div aria-hidden className="flex flex-col gap-3 px-6 pb-4">
        {[0, 1, 2].map((row) => (
          <Skeleton key={row} className="h-10 w-full" />
        ))}
      </div>
    </OverviewCard>
  );
}
