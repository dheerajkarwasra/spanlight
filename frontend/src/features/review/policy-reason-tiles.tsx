import { CircleAlert, Coins, Shuffle, Stethoscope, type LucideIcon } from "lucide-react";
import { useId } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import type { ReviewPolicy, ReviewPolicyInput } from "@/lib/api";

import { reasonLabel } from "./reason-copy";
import { policyInput, ratePercent } from "./policy-summary";
import { useSavePolicy } from "./review-queries";
import { reviewErrorMessage } from "./setup-steps";

/** The random share a re-enabled random toggle gets back: the server's default. */
const DEFAULT_RANDOM_RATE = "0.0100";

interface Tile {
  reason: "doctor_finding" | "error" | "cost_outlier" | "random";
  icon: LucideIcon;
  tone: "accent" | "danger" | "warning" | "outline";
  on: (policy: ReviewPolicy) => boolean;
  change: (on: boolean) => Partial<ReviewPolicyInput>;
  text: (policy: ReviewPolicy) => string;
}

const TILES: Tile[] = [
  {
    reason: "doctor_finding",
    icon: Stethoscope,
    tone: "accent",
    on: (policy) => policy.include_doctor_findings,
    change: (on) => ({ include_doctor_findings: on }),
    text: () => "Traces in an open Doctor finding",
  },
  {
    reason: "error",
    icon: CircleAlert,
    tone: "danger",
    on: (policy) => policy.include_errors,
    change: (on) => ({ include_errors: on }),
    text: () => "Traces with a failed span",
  },
  {
    reason: "cost_outlier",
    icon: Coins,
    tone: "warning",
    on: (policy) => policy.include_cost_outliers,
    change: (on) => ({ include_cost_outliers: on }),
    text: () => "Cost above the p99 of the trace name (7 days)",
  },
  {
    reason: "random",
    icon: Shuffle,
    tone: "outline",
    on: (policy) => Number(policy.random_rate) > 0,
    // shortcut: turning random back on restores 1 %, not the earlier share; the set-up dialog
    // edits the share. Keep the last share if people ask for it.
    change: (on) => ({ random_rate: on ? DEFAULT_RANDOM_RATE : "0.0000" }),
    text: (policy) =>
      Number(policy.random_rate) > 0
        ? `${ratePercent(policy.random_rate)} of the day's traces`
        : "No random share",
  },
];

interface PolicyReasonTilesProps {
  policy: ReviewPolicy;
  canWrite: boolean;
}

/** The four selection reasons as toggles; a toggle saves at once. */
export function PolicyReasonTiles({ policy, canWrite }: PolicyReasonTilesProps) {
  const save = useSavePolicy();
  return (
    <ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4" aria-label="Selection reasons">
      {TILES.map((tile) => (
        <TileRow
          key={tile.reason}
          tile={tile}
          policy={policy}
          disabled={!canWrite || save.isPending}
          onChange={(on) => {
            save.mutate(policyInput(policy, tile.change(on)), {
              onError: (error) => {
                toast.error(reviewErrorMessage(error));
              },
            });
          }}
        />
      ))}
    </ul>
  );
}

interface TileRowProps {
  tile: Tile;
  policy: ReviewPolicy;
  disabled: boolean;
  onChange: (on: boolean) => void;
}

function TileRow({ tile, policy, disabled, onChange }: TileRowProps) {
  const labelId = useId();
  const textId = useId();
  const Icon = tile.icon;
  return (
    <li className="flex min-w-0 flex-col gap-2 rounded-tile bg-surface-muted px-3.5 py-3">
      <div className="flex items-center justify-between gap-2">
        <Badge id={labelId} variant={tile.tone}>
          <Icon aria-hidden />
          {reasonLabel(tile.reason)}
        </Badge>
        <Switch
          checked={tile.on(policy)}
          disabled={disabled}
          aria-labelledby={labelId}
          aria-describedby={textId}
          onCheckedChange={onChange}
        />
      </div>
      <p id={textId} className="text-xs text-muted-foreground">
        {tile.text(policy)}
      </p>
    </li>
  );
}
