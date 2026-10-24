import { Search, Users } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { LiveDemoButton } from "@/features/auth";
import { KeySnippet } from "@/features/onboarding";

/**
 * No rows: a search that matched nobody, a window without end users, or a project that has never
 * received a trace (then the key snippet and the live demo help get started).
 */
export function UsersEmptyState({ q, unconfigured }: { q?: string; unconfigured: boolean }) {
  if (q) {
    return (
      <EmptyState
        icon={Search}
        title="No matching users"
        description={`No user ID in this window contains “${q}”.`}
      />
    );
  }
  return (
    <EmptyState
      icon={Users}
      title={unconfigured ? "No end users yet" : "No end users in this window"}
      description={
        <p>
          {unconfigured ? "No traces yet." : "No end users in this window."} Set{" "}
          <Code>user_id</Code> on your traces (SDK <Code>user_id=</Code>, OTLP <Code>user.id</Code>,
          gateway header <Code>x-spanlight-user</Code>).
        </p>
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

function Code({ children }: { children: string }) {
  return (
    <code className="rounded-xs border border-border bg-surface-muted px-1.5 py-0.5 font-mono text-label text-foreground">
      {children}
    </code>
  );
}
