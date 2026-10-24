import { Breadcrumbs, Crumb } from "@/components/breadcrumbs";
import { useProjectParams } from "@/features/shell";

/** "Experiments › <current>": back to the experiments list. */
export function ExperimentsBreadcrumbs({ current }: { current: string }) {
  const { orgId, projectId } = useProjectParams();
  return (
    <Breadcrumbs current={current}>
      <Crumb to="/$orgId/$projectId/experiments" params={{ orgId, projectId }}>
        Experiments
      </Crumb>
    </Breadcrumbs>
  );
}

/** "Experiments › <name> › <current>": back to one experiment, from a run or the comparison. */
export function ExperimentBreadcrumbs({
  experimentId,
  name,
  current,
}: {
  experimentId: string;
  name?: string;
  current: string;
}) {
  const { orgId, projectId } = useProjectParams();
  const label = name ?? "Experiment";
  return (
    <Breadcrumbs current={current}>
      <Crumb to="/$orgId/$projectId/experiments" params={{ orgId, projectId }}>
        Experiments
      </Crumb>
      <Crumb
        to="/$orgId/$projectId/experiments/$experimentId"
        params={{ orgId, projectId, experimentId }}
        title={label}
        className="max-w-40 sm:max-w-64"
      >
        <span className="truncate">{label}</span>
      </Crumb>
    </Breadcrumbs>
  );
}
