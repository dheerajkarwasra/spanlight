import { PromptVersionBadge } from "@/components/prompt-version-badge";
import { UnknownValue } from "@/components/unknown-value";
import { Skeleton } from "@/components/ui/skeleton";
import { usePromptVersionByIdQuery } from "@/features/prompts";
import { useProjectParams } from "@/features/shell";
import { isApiError } from "@/lib/api";

interface PromptVersionRefProps {
  /** The `prompt_version_id` a span carries. */
  versionId: string;
  /** A word before the version, e.g. "Prompt" among the trace's attribute chips. */
  prefix?: string;
  className?: string;
}

/**
 * The prompt version a span used, as a link to it. A span stores only the version id, so the
 * prompt's name and number are looked up; a version deleted since reads as unknown.
 */
export function PromptVersionRef({ versionId, prefix, className }: PromptVersionRefProps) {
  const { orgId, projectId } = useProjectParams();
  const version = usePromptVersionByIdQuery(versionId);
  if (version.isPending) {
    return <Skeleton aria-hidden className="h-6 w-36 rounded-full" />;
  }
  if (version.isError) {
    const gone = isApiError(version.error) && version.error.isNotFound;
    return (
      <span className="inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
        {prefix ?? "Prompt version"}
        <UnknownValue
          reason={
            gone
              ? "This prompt version no longer exists in the project."
              : "Couldn't load this prompt version."
          }
        />
      </span>
    );
  }
  return (
    <PromptVersionBadge
      orgId={orgId}
      projectId={projectId}
      name={version.data.prompt_name}
      version={version.data.version}
      prefix={prefix}
      className={className}
    />
  );
}
