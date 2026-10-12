import { useId } from "react";

import { useMe } from "@/features/auth";
import { usePermission } from "@/features/shell";
import type { Score, ScoreConfig } from "@/lib/api";
import { configChipText } from "@/lib/score-format";

import { groupScoresByName } from "./score-format";
import { ScoreRow } from "./score-row";

/** Mirrors the API: your own human score, or any score as an admin. */
function useCanDeleteScore(): (score: Score) => boolean {
  const me = useMe();
  const canWrite = usePermission("scores:write");
  const isAdmin = usePermission("project:write");
  return (score) =>
    canWrite && (isAdmin || (score.source === "human" && score.author_user_id === me.user.id));
}

interface ScoreGroupsProps {
  scores: readonly Score[];
  /** For the type chip beside each name; a name without a config shows no chip. */
  configs: readonly ScoreConfig[] | undefined;
}

/** Scores grouped by name, the group scored most recently first and each group latest first. */
export function ScoreGroups({ scores, configs }: ScoreGroupsProps) {
  const canDelete = useCanDeleteScore();
  const baseId = useId();
  return (
    <div className="flex flex-col gap-4">
      {groupScoresByName(scores).map((group, index) => {
        const config = configs?.find((candidate) => candidate.name === group.name);
        const headingId = `${baseId}-${String(index)}`;
        return (
          <section key={group.name} aria-labelledby={headingId} className="flex flex-col gap-2">
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <h3 id={headingId} className="text-sm font-semibold text-foreground">
                {group.name}
              </h3>
              {config ? (
                <span className="rounded-full border border-border-strong bg-surface px-[9px] py-[3px] text-2xs font-medium text-muted-foreground">
                  {configChipText(config)}
                </span>
              ) : null}
            </div>
            <ul aria-label={`${group.name} scores`} className="flex flex-col gap-2">
              {group.scores.map((score) => (
                <ScoreRow key={score.id} score={score} canDelete={canDelete(score)} />
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}
