import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { useProjectParams } from "@/features/shell";
import { toPathId, type Span, type TraceDetail } from "@/lib/api";
import { cn } from "@/lib/utils";

import { PromptVersionRef } from "./prompt-version-ref";

/** More prompt versions than one trace uses in practice; the span panel names every one. */
const MAX_PROMPT_VERSIONS = 3;

/** The distinct prompt versions the trace's spans used, in span order. */
function promptVersionIds(spans: readonly Span[]): string[] {
  const ids = spans.flatMap((span) => (span.prompt_version_id ? [span.prompt_version_id] : []));
  return [...new Set(ids)].slice(0, MAX_PROMPT_VERSIONS);
}

/**
 * Release, user, session, tags and the prompt versions used, as chips; chips for values the SDK
 * didn't send are omitted.
 */
export function TraceAttributeChips({ trace }: { trace: TraceDetail }) {
  const { orgId, projectId } = useProjectParams();
  const promptVersions = promptVersionIds(trace.spans);
  const hasAny =
    trace.release !== null ||
    trace.external_user_id !== null ||
    trace.session_id !== null ||
    trace.tags.length > 0 ||
    promptVersions.length > 0;
  if (!hasAny) {
    return null;
  }

  return (
    <dl className="flex flex-wrap items-center gap-2">
      {trace.release !== null ? (
        <Chip label="Release">
          <span title={trace.release} className="truncate font-mono text-label text-foreground">
            {trace.release}
          </span>
        </Chip>
      ) : null}
      {trace.external_user_id !== null ? (
        <Chip label="User">
          <Link
            to="/$orgId/$projectId/traces"
            params={{ orgId, projectId }}
            search={{ user: trace.external_user_id }}
            title={`Show traces from user ${trace.external_user_id}`}
            className="truncate rounded-sm font-mono text-label text-accent hover:underline"
          >
            {trace.external_user_id}
          </Link>
        </Chip>
      ) : null}
      {trace.session_id !== null ? (
        <Chip label="Session">
          <Link
            to="/$orgId/$projectId/sessions/$sessionId"
            params={{ orgId, projectId, sessionId: toPathId(trace.session_id) }}
            title={`Open session ${trace.session_id}`}
            className="truncate rounded-sm font-mono text-label text-accent hover:underline"
          >
            {trace.session_id}
          </Link>
        </Chip>
      ) : null}
      {trace.tags.length > 0 ? (
        <Chip label="Tags" className="py-1">
          <span className="flex flex-wrap gap-1">
            {trace.tags.map((tag) => (
              <Badge key={tag} variant="accent">
                {tag}
              </Badge>
            ))}
          </span>
        </Chip>
      ) : null}
      {promptVersions.map((versionId) => (
        <div key={versionId} className="flex max-w-full min-w-0">
          <dt className="sr-only">Prompt version</dt>
          <dd className="flex min-w-0">
            <PromptVersionRef versionId={versionId} prefix="Prompt" />
          </dd>
        </div>
      ))}
    </dl>
  );
}

function Chip({
  label,
  className,
  children,
}: {
  label: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div
      className={cn(
        "flex max-w-full min-w-0 items-center gap-1.5 rounded-full bg-surface-muted px-3 py-1",
        className,
      )}
    >
      <dt className="shrink-0 text-xs font-medium text-muted-foreground">{label}</dt>
      <dd className="flex min-w-0">{children}</dd>
    </div>
  );
}
