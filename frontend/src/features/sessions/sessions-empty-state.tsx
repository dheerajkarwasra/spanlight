import { MessagesSquare, Search } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { LiveDemoButton } from "@/features/auth";
import { KeySnippet } from "@/features/onboarding";
import { useProjectFilters } from "@/features/shell/project-context";
import { rangePhrase } from "@/lib/time-range";

/**
 * No rows: a search that matched nothing, a window or environment without sessions, or a project
 * that has never received a trace (then the key snippet and the live demo help get started).
 */
export function SessionsEmptyState({ q, unconfigured }: { q?: string; unconfigured: boolean }) {
  const { range, environment } = useProjectFilters();
  if (q) {
    return (
      <EmptyState
        icon={Search}
        title="No matching sessions"
        description={`No session ID starts with “${q}” and no user ID is “${q}” in this window.`}
      />
    );
  }
  let title = environment ? "No sessions in this environment" : "No sessions in this range";
  let lead = environment
    ? `Nothing from the "${environment}" environment was grouped into a session ${rangePhrase(range)}. Pick a wider time range or another environment.`
    : `Nothing was grouped into a session ${rangePhrase(range)}.`;
  if (unconfigured) {
    title = "No sessions yet";
    lead = "No traces yet.";
  }
  return (
    <EmptyState
      icon={MessagesSquare}
      title={title}
      description={
        <>
          <p>{lead} Sessions appear when traces carry a session ID, for example:</p>
          <code className="mt-2 inline-block rounded-xs border border-border bg-surface-muted px-2 py-0.5 font-mono text-label text-foreground">
            update_trace(session_id=&quot;chat_123&quot;)
          </code>
        </>
      }
      setup={
        unconfigured ? (
          <>
            <KeySnippet code={(keyLine) => `${keyLine}  # your key from Settings › API keys`} />
            <LiveDemoButton />
          </>
        ) : undefined
      }
    />
  );
}
