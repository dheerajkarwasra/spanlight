import { BackLink } from "@/components/back-link";
import { useProjectParams } from "@/features/shell";

import { usePromptLink } from "./use-prompt-links";

/** Back to the prompt list. */
export function BackToPrompts() {
  const { orgId, projectId } = useProjectParams();
  return (
    <BackLink to="/$orgId/$projectId/prompts" params={{ orgId, projectId }}>
      Prompts
    </BackLink>
  );
}

/** Back to one prompt's versions, from the editor or the diff. */
export function BackToPrompt({ name, version }: { name: string; version?: number }) {
  const link = usePromptLink(name, { v: version });
  return <BackLink {...link}>{name}</BackLink>;
}
