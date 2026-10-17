import { cn } from "@/lib/utils";

import { IlloHeader, IlloRow, Pill } from "./illo-parts";

/** Share of the widest bar, not amounts: the drawing shows the grouping, not real spend. */
const GROUPS = [
  { key: "acme", share: 82 },
  { key: "globex", share: 46 },
  { key: "none", share: 18 },
] as const;

/** Spend grouped by a metadata key, and a blocking budget on one value (Figma "Illo/Attribution"). */
export function AttributionIllustration() {
  return (
    <div className="flex flex-col gap-3">
      <IlloHeader
        label={
          <>
            Spend by <span className="font-mono normal-case">customer_id</span>
          </>
        }
      >
        <Pill className="hidden md:inline">rows add up to the total</Pill>
      </IlloHeader>
      {GROUPS.map((group) => (
        <div key={group.key} className="flex items-center gap-3">
          <span className="w-16 shrink-0 font-mono text-label text-foreground">{group.key}</span>
          <span className="h-2.5 flex-1 rounded-full bg-surface-muted">
            <span
              className={cn(
                "block h-full rounded-full",
                group.key === "none" ? "bg-subtle-foreground" : "bg-chart-4",
              )}
              style={{ width: `${group.share}%` }}
            />
          </span>
        </div>
      ))}
      <IlloRow tone="outline" dot="warning" label="budget · customer_id=acme">
        <Pill tone="warning" className="hidden md:inline">
          Blocking (soft)
        </Pill>
        <Pill tone="danger">402</Pill>
      </IlloRow>
    </div>
  );
}
