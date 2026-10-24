import { Link } from "@tanstack/react-router";
import { Info } from "lucide-react";
import { useId } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { useProjectParams } from "@/features/shell";
import type { ScoreConfig } from "@/lib/api";
import { configChipText } from "@/lib/score-format";
import { cn } from "@/lib/utils";

/** The backend's limit on scores per queue. */
const MAX_CONFIGS = 20;

interface ScoreConfigPickerProps {
  /** Undefined while the configs load. */
  configs: readonly ScoreConfig[] | undefined;
  picked: readonly string[];
  onChange: (picked: readonly string[]) => void;
}

/** "Scores to collect": one checkbox row per score config of the project. */
export function ScoreConfigPicker({ configs, picked, onChange }: ScoreConfigPickerProps) {
  const hintId = useId();
  const params = useProjectParams();
  const full = picked.length >= MAX_CONFIGS;

  function toggle(id: string, checked: boolean) {
    onChange(checked ? [...picked, id] : picked.filter((pickedId) => pickedId !== id));
  }

  return (
    <fieldset className="flex flex-col gap-2" aria-describedby={hintId}>
      <legend className="pb-2 text-label font-semibold text-foreground">Scores to collect</legend>
      {configs === undefined ? (
        <div role="status" aria-label="Loading score configs" className="flex flex-col gap-1.5">
          <Skeleton className="h-11 rounded-input" />
          <Skeleton className="h-11 rounded-input" />
        </div>
      ) : configs.length === 0 ? (
        <p className="rounded-input bg-surface-muted px-3.5 py-3 text-sm text-muted-foreground">
          This project has no score configs yet. Create one under{" "}
          <Link
            to="/$orgId/$projectId/settings/project"
            params={params}
            hash="score-configs"
            className="rounded-xs font-semibold text-accent underline underline-offset-2"
          >
            Settings
          </Link>
          , or send a score from the SDK, then come back.
        </p>
      ) : (
        <ul className="flex max-h-64 flex-col gap-1.5 overflow-y-auto">
          {configs.map((config) => {
            const checked = picked.includes(config.id);
            return (
              <li key={config.id}>
                <label
                  className={cn(
                    "flex min-h-11 cursor-pointer items-center gap-3 rounded-input px-3.5 py-2 transition-colors",
                    checked
                      ? "border-2 border-accent bg-surface-selected"
                      : "border border-border-strong bg-surface hover:bg-surface-hover",
                  )}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    disabled={!checked && full}
                    onChange={(event) => toggle(config.id, event.target.checked)}
                    className="size-4 shrink-0 rounded-checkbox accent-accent"
                  />
                  <span className="flex-1 font-mono text-label font-semibold text-foreground">
                    {config.name}
                  </span>
                  <span className="text-right text-2xs font-medium text-muted-foreground">
                    {configChipText(config)}
                  </span>
                </label>
              </li>
            );
          })}
        </ul>
      )}
      <p
        id={hintId}
        className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground"
      >
        <Info aria-hidden className="size-3.5 shrink-0" />
        {full
          ? `A queue collects at most ${String(MAX_CONFIGS)} scores.`
          : "Every selected score is required to finish an item."}
      </p>
    </fieldset>
  );
}
