import { Link } from "@tanstack/react-router";
import { FileText } from "lucide-react";

import { toPathPromptName } from "@/lib/api";
import { formatPromptVersion } from "@/lib/format";
import { cn } from "@/lib/utils";

interface PromptVersionBadgeProps {
  orgId: string;
  projectId: string;
  name: string;
  version: number;
  /** A word before the version, e.g. "Prompt" where the badge sits among other attributes. */
  prefix?: string;
  className?: string;
}

/**
 * Figma "Prompts/Version chip": a violet pill naming a prompt version ("support/answer v3") that
 * opens that version on the prompt's page.
 */
export function PromptVersionBadge({
  orgId,
  projectId,
  name,
  version,
  prefix,
  className,
}: PromptVersionBadgeProps) {
  const text = formatPromptVersion(name, version);
  return (
    <Link
      to="/$orgId/$projectId/prompts/$name"
      params={{ orgId, projectId, name: toPathPromptName(name) }}
      search={{ v: version }}
      title={`Open prompt ${text}`}
      className={cn(
        "inline-flex max-w-full min-w-0 items-center gap-1 rounded-full bg-accent-subtle px-2.5 py-1 text-xs font-semibold text-accent transition-colors hover:underline",
        className,
      )}
    >
      <FileText aria-hidden className="size-3.5 shrink-0" />
      <span className="truncate">{prefix ? `${prefix} ${text}` : text}</span>
    </Link>
  );
}
