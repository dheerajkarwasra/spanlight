import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { getRouteApi, Link } from "@tanstack/react-router";
import { ArrowLeft } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { ErrorState } from "@/components/error-state";
import { Notice } from "@/components/notice";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useMe } from "@/features/auth/queries";
import { orgsApi, queryKeys, type Membership, type OnboardingStatus } from "@/lib/api";
import { readLastProject, type LastProject } from "@/lib/last-project";

import { ConnectStep } from "./connect-step";
import { ONBOARDING_COPY } from "./copy";
import { OnboardingLayout } from "./onboarding-layout";
import { OnboardingStepper } from "./onboarding-stepper";
import { OrgStep } from "./org-step";
import { ProjectStep } from "./project-step";
import { StepHero, type StepHeroCopy } from "./step-hero";
import {
  resolveOnboardingState,
  type OnboardingProgress,
  type OnboardingState,
  type OnboardingStepId,
} from "./steps";
import { useOnboardingStatus } from "./use-onboarding-status";

const onboardingRoute = getRouteApi("/_authed/onboarding");

const HEADING_ID = "onboarding-step-heading";

export function OnboardingPage() {
  const search = onboardingRoute.useSearch();
  const me = useMe();

  const isOrgMember = me.memberships.some((membership) => membership.org.id === search.org);
  const projects = useQuery({
    queryKey: queryKeys.org(search.org ?? "").projects,
    queryFn: () => orgsApi.projects(search.org ?? ""),
    enabled: isOrgMember && search.project !== undefined,
  });

  const state = resolveOnboardingState({
    search,
    memberships: me.memberships,
    projects: projects.data,
  });

  const status = useOnboardingStatus(state.step === "connect" ? state.project.id : null);
  const firstTraceReceived = state.step === "connect" && status.data?.has_traces === true;
  const loadFailed = projects.isError && state.step === "loading";

  return (
    <OnboardingLayout
      stepper={loadFailed ? null : <Stepper state={state} complete={firstTraceReceived} />}
      exitLink={firstTraceReceived ? null : <ExitLink state={state} memberships={me.memberships} />}
    >
      {loadFailed ? (
        <ErrorState
          error={projects.error}
          title="Couldn't load this organization's projects"
          onRetry={() => {
            void projects.refetch();
          }}
        />
      ) : (
        <OnboardingStep state={state} memberships={me.memberships} status={status} />
      )}
    </OnboardingLayout>
  );
}

/** The step pills with real progress; a placeholder while step 3 is being verified. */
function Stepper({ state, complete }: { state: OnboardingState; complete: boolean }) {
  if (state.step === "loading") {
    return <Skeleton className="h-[38px] w-[min(458px,100%)] rounded-full" />;
  }
  const progress: OnboardingProgress = complete ? "complete" : state.step;
  return <OnboardingStepper progress={progress} />;
}

interface OnboardingStepProps {
  state: OnboardingState;
  memberships: readonly Membership[];
  status: UseQueryResult<OnboardingStatus>;
}

function OnboardingStep({ state, memberships, status }: OnboardingStepProps) {
  switch (state.step) {
    case "loading":
      return <StepSkeleton />;

    case "org":
      return (
        <StepSection
          step="org"
          notice={state.notice}
          hero={{
            title: "Set up in three",
            accent: "steps.",
            description: ONBOARDING_COPY.org.heroDescription,
          }}
        >
          <OrgStep memberships={memberships} />
        </StepSection>
      );

    case "project":
      return (
        <StepSection
          step="project"
          notice={state.notice}
          hero={{
            title: `${state.membership.org.name} is`,
            accent: "ready.",
            description: ONBOARDING_COPY.project.heroDescription,
          }}
        >
          <ProjectStep membership={state.membership} />
        </StepSection>
      );

    case "connect": {
      const received = status.data?.has_traces === true;
      const hero: StepHeroCopy = received
        ? {
            title: "You’re",
            accent: "connected.",
            description: `Spanlight is recording traces from ${state.project.name}.`,
          }
        : { title: "Send your first", accent: "trace." };
      return (
        <StepSection step="connect" notice={null} hero={hero} compact>
          <ConnectStep membership={state.membership} project={state.project} status={status} />
        </StepSection>
      );
    }
  }
}

interface StepSectionProps {
  step: OnboardingStepId;
  hero: StepHeroCopy;
  notice: string | null;
  /** Step 3 stacks several cards, so it uses the tighter 20px rhythm from the design. */
  compact?: boolean;
  children: ReactNode;
}

/** Headline + content for one step. Moves focus to the headline when the step changes. */
function StepSection({ step, hero, notice, compact = false, children }: StepSectionProps) {
  const headingRef = useRef<HTMLHeadingElement>(null);
  const previousStep = useRef(step);

  useEffect(() => {
    if (previousStep.current !== step) {
      previousStep.current = step;
      headingRef.current?.focus();
    }
  }, [step]);

  return (
    <section
      aria-labelledby={HEADING_ID}
      className={compact ? "flex min-w-0 flex-col gap-5" : "flex min-w-0 flex-col gap-8"}
    >
      <StepHero id={HEADING_ID} headingRef={headingRef} {...hero} />
      {notice ? (
        <Notice tone="warning" role="alert">
          {notice}
        </Notice>
      ) : null}
      {children}
    </section>
  );
}

/** Placeholder shaped like a step: the two-line headline, then the step card. */
function StepSkeleton() {
  return (
    <div className="flex flex-col gap-8" aria-busy="true" aria-label="Loading">
      <div className="flex flex-col gap-3">
        <Skeleton className="h-11 w-80 max-w-full" />
        <Skeleton className="h-5 w-96 max-w-full" />
      </div>
      <Skeleton className="h-72 w-full rounded-card" />
    </div>
  );
}

/**
 * "Skip to dashboard" once this flow has a project, or "Back to dashboard"
 * when the user arrived here from an existing project. Hidden once the first trace arrived:
 * the success card links into the app instead.
 */
function ExitLink({
  state,
  memberships,
}: {
  state: OnboardingState;
  memberships: readonly Membership[];
}) {
  const [lastProject] = useState<LastProject | null>(readLastProject);

  if (state.step === "connect") {
    return (
      <Button asChild variant="ghost" size="sm">
        <Link
          to="/$orgId/$projectId/overview"
          params={{ orgId: state.membership.org.id, projectId: state.project.id }}
        >
          Skip to dashboard
        </Link>
      </Button>
    );
  }

  const canGoBack =
    lastProject !== null &&
    memberships.some((membership) => membership.org.id === lastProject.orgId);
  if (!canGoBack) {
    return null;
  }
  return (
    <Button asChild variant="ghost" size="sm">
      <Link
        to="/$orgId/$projectId/overview"
        params={{ orgId: lastProject.orgId, projectId: lastProject.projectId }}
      >
        <ArrowLeft aria-hidden />
        Back to dashboard
      </Link>
    </Button>
  );
}
