import { Breadcrumbs, Crumb } from "@/components/breadcrumbs";
import { useProjectParams } from "@/features/shell";

import { usePromptLink } from "./use-prompt-links";

/** "Prompts › <current>": back to the prompt list. */
export function PromptsBreadcrumbs({ current }: { current: string }) {
  const { orgId, projectId } = useProjectParams();
  return (
    <Breadcrumbs current={current}>
      <Crumb to="/$orgId/$projectId/prompts" params={{ orgId, projectId }}>
        Prompts
      </Crumb>
    </Breadcrumbs>
  );
}

/** "Prompts › <name> › <current>": back to one prompt's versions, from the editor or the diff. */
export function PromptBreadcrumbs({
  name,
  version,
  current,
}: {
  name: string;
  version?: number;
  current: string;
}) {
  const { orgId, projectId } = useProjectParams();
  const link = usePromptLink(name, { v: version });
  return (
    <Breadcrumbs current={current}>
      <Crumb to="/$orgId/$projectId/prompts" params={{ orgId, projectId }}>
        Prompts
      </Crumb>
      <Crumb {...link} title={name} className="max-w-40 sm:max-w-64">
        <span className="truncate">{name}</span>
      </Crumb>
    </Breadcrumbs>
  );
}
