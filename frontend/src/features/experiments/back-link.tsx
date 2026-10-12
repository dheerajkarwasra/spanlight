import { BackLink } from "@/components/back-link";
import { useProjectParams } from "@/features/shell";

/** Back to the experiments list. */
export function BackToExperiments() {
  const { orgId, projectId } = useProjectParams();
  return (
    <BackLink to="/$orgId/$projectId/experiments" params={{ orgId, projectId }}>
      Experiments
    </BackLink>
  );
}

/** Back to one experiment, from a run or the comparison. */
export function BackToExperiment({ experimentId, name }: { experimentId: string; name?: string }) {
  const { orgId, projectId } = useProjectParams();
  return (
    <BackLink
      to="/$orgId/$projectId/experiments/$experimentId"
      params={{ orgId, projectId, experimentId }}
    >
      {name ?? "Experiment"}
    </BackLink>
  );
}
