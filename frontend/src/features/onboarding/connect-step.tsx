import type { UseQueryResult } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import type { CreatedApiKey, Membership, OnboardingStatus, Project } from "@/lib/api";
import { can } from "@/lib/permissions";

import { ApiKeyCard } from "./api-key-card";
import { firstTraceAction } from "./first-trace";
import { FirstTraceCard, WaitingForTrace } from "./first-trace-status";
import { GatewaySetupCard } from "./gateway-setup-card";
import { SnippetTabs } from "./snippet-tabs";
import { DEFAULT_ENVIRONMENT, type SnippetId } from "./snippets";
import { newTestTraceIds } from "./test-trace";
import { TestTraceButton } from "./test-trace-button";
import { useFirstTrace } from "./use-onboarding-status";

interface ConnectStepProps {
  membership: Membership;
  project: Project;
  /** The polling onboarding status, owned by the page so the step pills can show completion. */
  status: UseQueryResult<OnboardingStatus>;
}

/**
 * Step 3. While waiting: API key card, snippets, then the live "waiting" row. Once the first
 * trace arrives the success card moves to the top. The key card stays while its secret is on
 * screen, because it can't be shown again; without one, the first trace opens in trace detail.
 */
export function ConnectStep({ membership, project, status }: ConnectStepProps) {
  // The secret is only returned once, so it lives here and is never cached.
  const [createdKey, setCreatedKey] = useState<CreatedApiKey | null>(null);
  const [snippetId, setSnippetId] = useState<SnippetId>("python");
  // One key per mount (it is never cleared), so one set of test trace ids per key. Kept here,
  // not in the button, so switching tabs and back reuses them.
  const [testIds] = useState(newTestTraceIds);
  const [testSent, setTestSent] = useState(false);
  const received = status.data?.has_traces === true;
  // Auto-open only for an arrival seen on this visit, not for a project that already had traces.
  const [waitedHere, setWaitedHere] = useState(false);
  if (status.data?.has_traces === false && !waitedHere) {
    setWaitedHere(true);
  }
  const firstTraceAt = received ? (status.data?.first_trace_at ?? null) : null;
  const firstTrace = useFirstTrace(project.id, firstTraceAt);
  const traceId = firstTrace.data?.trace_id ?? null;
  const action = firstTraceAction({
    secretOnScreen: createdKey !== null,
    arrivedThisVisit: waitedHere,
  });

  const navigate = useNavigate();
  // Re-renders and StrictMode's second effect run must not open it twice.
  const opened = useRef(false);
  useEffect(() => {
    if (action !== "open" || traceId === null || opened.current) {
      return;
    }
    opened.current = true;
    toast.success("Your first trace arrived.");
    void navigate({
      to: "/$orgId/$projectId/traces/$traceId",
      params: { orgId: membership.org.id, projectId: project.id, traceId },
      replace: true,
    });
  }, [action, traceId, navigate, membership.org.id, project.id]);

  const keyCard = (
    <ApiKeyCard
      projectId={project.id}
      projectName={project.name}
      role={membership.role}
      canCreateKeys={can(membership.role, "key:create")}
      createdKey={createdKey}
      onCreated={setCreatedKey}
      testTrace={
        <TestTraceButton
          apiKey={createdKey?.secret ?? null}
          ids={testIds}
          sent={testSent}
          environment={DEFAULT_ENVIRONMENT}
          onSent={() => {
            setTestSent(true);
          }}
        />
      }
    />
  );

  return (
    <div className="flex flex-col gap-5">
      {/* Announces the switch to the success state; the visible cards live in different places. */}
      <p role="status" className="sr-only">
        {received ? "Connected. Your first trace has arrived." : ""}
      </p>

      {received ? (
        <FirstTraceCard
          orgId={membership.org.id}
          projectId={project.id}
          firstTraceAt={firstTraceAt}
        />
      ) : null}

      {snippetId === "gateway" ? (
        <GatewaySetupCard orgId={membership.org.id} projectId={project.id} />
      ) : received && createdKey === null ? null : (
        keyCard
      )}

      <SnippetTabs
        apiKey={createdKey?.secret ?? null}
        value={snippetId}
        onValueChange={setSnippetId}
        orgId={membership.org.id}
        projectId={project.id}
      />

      {received ? null : (
        <WaitingForTrace
          gateway={snippetId === "gateway"}
          testSent={testSent}
          error={status.isError && !status.data ? status.error : null}
          onRetry={() => {
            void status.refetch();
          }}
        />
      )}
    </div>
  );
}
