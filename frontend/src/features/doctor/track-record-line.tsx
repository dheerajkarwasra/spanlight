import { Target } from "lucide-react";

import { Card } from "@/components/ui/card";

import { usePrecision } from "./doctor-queries";
import { formatPrecisionRow } from "./precision-format";
import { PrecisionStatusMark } from "./precision-table";
import { trackRecordText } from "./track-record";

/**
 * Figma "Doctor/Track record": how often this finding's check was right, from members' votes on
 * every finding of its kind, with "Meets target" or "Below target" once there are 20 votes.
 */
export function TrackRecordLine({ kind }: { kind: string }) {
  const precision = usePrecision();
  const row = precision.data?.kinds.find((candidate) => candidate.kind === kind);
  const view = row === undefined ? null : formatPrecisionRow(row);
  return (
    <Card
      role="region"
      aria-label="Track record"
      className="flex flex-wrap items-center gap-x-3 gap-y-2 px-5 py-4 sm:px-6"
    >
      <Target aria-hidden className="size-4 shrink-0 text-muted-foreground" />
      <p className="text-sm font-medium text-foreground tabular">
        {precision.isPending
          ? "Loading the track record…"
          : precision.isError
            ? "The track record could not be loaded."
            : trackRecordText(row)}
      </p>
      {view?.status === "meets" || view?.status === "below" ? (
        <PrecisionStatusMark view={view} />
      ) : null}
    </Card>
  );
}
