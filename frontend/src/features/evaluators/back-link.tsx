import { BackLink } from "@/components/back-link";
import { useProjectParams } from "@/features/shell";

/** "← Evaluators": back to the list. */
export function BackToEvaluators() {
  const { orgId, projectId } = useProjectParams();
  return (
    <BackLink to="/$orgId/$projectId/evaluators" params={{ orgId, projectId }}>
      Evaluators
    </BackLink>
  );
}
