import { Breadcrumbs, Crumb } from "@/components/breadcrumbs";
import { useProjectParams } from "@/features/shell/project-context";

import { recallTracesSearch } from "./last-traces-search";

/** "Traces › <current>": back to the list with its remembered search. */
export function TracesBreadcrumbs({ current }: { current: string }) {
  const { orgId, projectId } = useProjectParams();
  return (
    <Breadcrumbs current={current}>
      <Crumb
        to="/$orgId/$projectId/traces"
        params={{ orgId, projectId }}
        search={recallTracesSearch()}
      >
        Traces
      </Crumb>
    </Breadcrumbs>
  );
}
