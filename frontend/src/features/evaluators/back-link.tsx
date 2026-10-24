import { Breadcrumbs, Crumb } from "@/components/breadcrumbs";
import { useProjectParams } from "@/features/shell";
import type { Evaluator } from "@/lib/api";

interface EvaluatorsBreadcrumbsProps {
  current: string;
  /** The evaluator a sub-page belongs to, shown as the middle crumb. */
  evaluator?: Pick<Evaluator, "id" | "name">;
}

/** "Evaluators › <current>", or "Evaluators › <name> › <current>" under one evaluator. */
export function EvaluatorsBreadcrumbs({ current, evaluator }: EvaluatorsBreadcrumbsProps) {
  const { orgId, projectId } = useProjectParams();
  return (
    <Breadcrumbs current={current}>
      <Crumb to="/$orgId/$projectId/evaluators" params={{ orgId, projectId }}>
        Evaluators
      </Crumb>
      {evaluator ? (
        <Crumb
          to="/$orgId/$projectId/evaluators/$evaluatorId"
          params={{ orgId, projectId, evaluatorId: evaluator.id }}
          title={evaluator.name}
          className="max-w-40 sm:max-w-64"
        >
          <span className="truncate">{evaluator.name}</span>
        </Crumb>
      ) : null}
    </Breadcrumbs>
  );
}
