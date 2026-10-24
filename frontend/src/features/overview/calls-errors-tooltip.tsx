import { ChartTooltip } from "@/components/chart-parts";
import type { Bucket } from "@/lib/api";
import { SERIES_COLORS } from "@/lib/chart-theme";
import { formatInteger, formatPercent } from "@/lib/format";
import type { Band } from "@/lib/incident-bands";

import { formatBucketRange, type ChartPoint } from "./chart-data";
import { IncidentKey } from "./incident-bands";
import { incidentsAt } from "./incident-overlay";

interface BucketTooltipProps {
  active: boolean | undefined;
  point: ChartPoint;
  bucket: Bucket;
  now: Date;
  bands: readonly Band[];
}

/** One bucket's calls, failed calls with their rate, and the incidents that overlap it. */
export function BucketTooltip({ active, point, bucket, now, bands }: BucketTooltipProps) {
  const rate = point.llmCalls > 0 ? point.errors / point.llmCalls : null;
  const errorValue = formatInteger(point.errors) ?? "0";
  return (
    <ChartTooltip
      active={active}
      title={formatBucketRange(point.bucketStart, bucket, now, point.partial)}
      rows={[
        { label: "Calls", value: formatInteger(point.llmCalls), color: SERIES_COLORS.calls },
        {
          label: "Failed",
          value: rate === null ? errorValue : `${errorValue} · ${formatPercent(rate) ?? ""}`,
          color: SERIES_COLORS.errors,
        },
      ]}
      footer={<IncidentLines titles={incidentsAt(bands, point.bucketStart, bucket)} />}
    />
  );
}

/** The tooltip's incident block: the overlapping incidents' titles, text only. */
function IncidentLines({ titles }: { titles: string[] }) {
  if (titles.length === 0) {
    return null;
  }
  return (
    <div className="mt-2 flex max-w-60 flex-col gap-1">
      <p className="flex items-center gap-1.5 text-rail-muted-foreground">
        <IncidentKey />
        {titles.length === 1 ? "Provider incident" : "Provider incidents"}
      </p>
      {titles.map((title) => (
        <p key={title} className="font-medium">
          {title}
        </p>
      ))}
    </div>
  );
}
