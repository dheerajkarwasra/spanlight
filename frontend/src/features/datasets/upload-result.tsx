import { Check, TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";

import type { UploadResult } from "@/lib/api";
import { formatInteger } from "@/lib/format";
import { cn } from "@/lib/utils";

/** Figma "Upload finished": accepted and rejected counts, then each rejected line and why. */
export function UploadResultView({ result }: { result: UploadResult }) {
  const rejectedCount = result.rejected.length;
  return (
    <div className="flex flex-col gap-4" role="status">
      <div className="grid grid-cols-2 gap-2.5">
        <CountTile
          tone="success"
          icon={<Check aria-hidden className="size-3.5" />}
          label="Accepted"
          value={result.accepted}
        />
        <CountTile
          tone={rejectedCount > 0 ? "danger" : "neutral"}
          icon={<TriangleAlert aria-hidden className="size-3.5" />}
          label="Rejected"
          value={rejectedCount}
        />
      </div>
      {rejectedCount > 0 ? (
        <div className="flex flex-col gap-2">
          <div className="flex items-baseline justify-between gap-2 text-xs">
            <p className="font-semibold text-foreground">Rejected lines</p>
            <p className="font-medium text-muted-foreground">
              Showing {formatInteger(rejectedCount)} of {formatInteger(rejectedCount)}
            </p>
          </div>
          <ul className="flex max-h-64 flex-col gap-1 overflow-y-auto">
            {result.rejected.map((line) => (
              <li
                key={line.line}
                className="flex items-baseline gap-4 rounded-input bg-surface-muted px-3 py-2 text-xs"
              >
                <span className="w-16 shrink-0 font-mono text-foreground">Line {line.line}</span>
                <span className="min-w-0 font-medium [overflow-wrap:anywhere] text-foreground">
                  {line.reason}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

const TILE_TONES = {
  success: "border-success bg-success-subtle text-success",
  danger: "border-danger bg-danger-subtle text-danger-text",
  neutral: "border-border bg-surface-muted text-muted-foreground",
} as const;

function CountTile({
  tone,
  icon,
  label,
  value,
}: {
  tone: keyof typeof TILE_TONES;
  icon: ReactNode;
  label: string;
  value: number;
}) {
  return (
    <div className={cn("flex flex-col gap-1 rounded-tile border px-3.5 py-3", TILE_TONES[tone])}>
      <span className="flex items-center gap-1.5 text-xs font-semibold">
        {icon}
        {label}
      </span>
      <span className="text-[1.5rem] leading-tight font-bold text-foreground tabular">
        {formatInteger(value)}
      </span>
    </div>
  );
}
