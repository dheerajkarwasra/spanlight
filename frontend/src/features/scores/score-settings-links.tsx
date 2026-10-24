import { Link } from "@tanstack/react-router";

import { useProjectParams } from "@/features/shell";

const LINK = "rounded-xs font-semibold text-accent underline underline-offset-2";

/** "Scores use the score configs and failure categories in Settings › Project.", both linked. */
export function ScoreSettingsLinks() {
  const params = useProjectParams();
  return (
    <>
      Scores use the{" "}
      <Link
        to="/$orgId/$projectId/settings/project"
        params={params}
        hash="score-configs"
        className={LINK}
      >
        score configs
      </Link>{" "}
      and{" "}
      <Link
        to="/$orgId/$projectId/settings/project"
        params={params}
        hash="failure-categories"
        className={LINK}
      >
        failure categories
      </Link>{" "}
      in Settings › Project.
    </>
  );
}
