import { Link } from "@tanstack/react-router";
import { Activity, Check } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { LiveDemoButton } from "@/features/auth";
import { useProjectParams } from "@/features/shell/project-context";

import { EmptyKpiCards } from "./kpi-card-placeholders";
import { SnippetPanel } from "./snippet-panel";

interface FirstRunProps {
  projectName: string | null;
  environment: string | undefined;
}

/**
 * The project has never received a trace (Figma "Overview — First run"): a connect card with
 * the three setup steps and a live snippet, then the KPI cards with nothing in them yet. The
 * page polls for the first trace and swaps to the dashboard on its own.
 */
export function FirstRun({ projectName, environment }: FirstRunProps) {
  return (
    <>
      <ConnectCard projectName={projectName} environment={environment} />
      <EmptyKpiCards />
    </>
  );
}

function ConnectCard({ projectName, environment }: FirstRunProps) {
  const { orgId, projectId } = useProjectParams();

  return (
    <Card className="flex flex-col gap-8 overflow-hidden p-6 sm:p-9 xl:flex-row xl:gap-12">
      <div className="relative flex flex-col gap-7 xl:w-[430px] xl:shrink-0">
        <Illustration />
        <div className="flex flex-col gap-2.5">
          <h2 className="text-h2 text-foreground">Connect your app to start tracing</h2>
          <p className="text-sm text-muted-foreground">
            Add the Python SDK, wrap your OpenAI or Anthropic client, or point any OpenTelemetry
            exporter at Spanlight. Traces show up here within seconds.
          </p>
        </div>
        <SetupSteps projectName={projectName} environment={environment} />
        <div className="flex flex-wrap gap-2.5">
          <Button asChild variant="primary" size="lg">
            <Link to="/onboarding" search={{ org: orgId, project: projectId }}>
              Connect your app
            </Link>
          </Button>
          <LiveDemoButton />
        </div>
      </div>
      <SnippetPanel environment={environment} />
    </Card>
  );
}

function Illustration() {
  return (
    <div aria-hidden className="relative h-[104px] w-[132px]">
      <span className="absolute top-5 left-[34px] flex size-16 items-center justify-center rounded-[20px] border border-border bg-surface shadow-lg">
        <Activity className="size-7 text-accent" strokeWidth={2} />
      </span>
      <span className="absolute top-3.5 left-[90px] size-3.5 rounded-full border-[3px] border-surface bg-lime" />
    </div>
  );
}

interface SetupStepsProps {
  projectName: string | null;
  environment: string | undefined;
}

function SetupSteps({ projectName, environment }: SetupStepsProps) {
  const projectDetail = [projectName, environment].filter(Boolean).join(" · ");
  return (
    <ol className="flex flex-col gap-1.5">
      <li className="flex items-center gap-3.5 rounded-tile px-3 py-2.5">
        <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-success-subtle text-success">
          <Check aria-hidden className="size-3.5" strokeWidth={2.5} />
        </span>
        <span className="flex min-w-0 flex-col">
          <span className="text-sm font-semibold text-foreground">
            Create a project
            <span className="sr-only"> (done)</span>
          </span>
          {projectDetail ? (
            <span className="truncate text-xs font-medium text-subtle-foreground">
              {projectDetail}
            </span>
          ) : null}
        </span>
      </li>
      <li
        aria-current="step"
        className="flex items-center gap-3.5 rounded-tile bg-surface-muted px-3 py-2.5"
      >
        <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-ink text-sm font-semibold text-ink-foreground">
          2
        </span>
        <span className="flex min-w-0 flex-col">
          <span className="text-sm font-semibold text-foreground">
            Install the SDK and add your API key
          </span>
          <span className="text-xs font-medium text-subtle-foreground">
            pip install spanlight, then call init()
          </span>
        </span>
      </li>
      <li className="flex items-center gap-3.5 rounded-tile px-3 py-2.5">
        <span className="flex size-7 shrink-0 items-center justify-center rounded-full border border-border bg-surface text-sm font-semibold text-muted-foreground">
          3
        </span>
        <span className="flex min-w-0 flex-col">
          <span className="text-sm font-semibold text-muted-foreground">Send a trace</span>
          <span className="text-xs font-medium text-subtle-foreground">
            Run your app — this page updates on its own
          </span>
        </span>
      </li>
    </ol>
  );
}
