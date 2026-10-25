import type { Activation } from "@/lib/api";
import { docsLink } from "@/lib/docs-links";
import type { Permission } from "@/lib/permissions";

/** A link in a step's guide: onboarding, a page of the current project, or the docs. */
export type StepLink =
  | { label: string; to: "/onboarding" }
  | {
      label: string;
      to:
        | "/$orgId/$projectId/sessions"
        | "/$orgId/$projectId/budgets"
        | "/$orgId/$projectId/evaluators"
        | "/$orgId/$projectId/queues";
      /** Without this permission the link is replaced by the `otherwise` sentence. */
      requires?: Permission;
      otherwise?: string;
    }
  | { label: string; href: string };

export interface ActivationStep {
  id: keyof Activation;
  label: string;
  guide: string;
  links: readonly StepLink[];
}

/** The setup checklist, in order (UX Phase 6 spec §5.2; copy is binding). */
export const ACTIVATION_STEPS: readonly ActivationStep[] = [
  {
    id: "first_trace",
    label: "Send a trace from your app",
    guide:
      "Add the SDK or an OTLP exporter to your app. Your traces show up in Traces within seconds.",
    links: [{ label: "Connect your app", to: "/onboarding" }],
  },
  {
    id: "session",
    label: "Group traces into a session",
    guide:
      "Pass a session ID with each call in a conversation. Spanlight joins those traces into one session.",
    links: [
      { label: "Open Sessions", to: "/$orgId/$projectId/sessions" },
      {
        label: "How sessions work",
        href: docsLink("guides/traces/#follow-a-conversation-in-sessions"),
      },
    ],
  },
  {
    id: "budget",
    label: "Set a budget",
    guide: "Choose a monthly limit for this project. Spanlight warns you before spend reaches it.",
    links: [
      {
        label: "Open Budgets",
        to: "/$orgId/$projectId/budgets",
        requires: "alerts:write",
        otherwise: "Ask an admin to set one.",
      },
    ],
  },
  {
    id: "evaluation",
    label: "Run an eval or a review",
    guide: "Score traces with an LLM judge, or grade them yourself in Review.",
    links: [
      { label: "Open Evaluators", to: "/$orgId/$projectId/evaluators" },
      { label: "Open Review", to: "/$orgId/$projectId/queues" },
    ],
  },
];

export function doneCount(activation: Activation): number {
  return ACTIVATION_STEPS.filter((step) => activation[step.id]).length;
}

/**
 * Ruling 12: after the first trace (before it the "Connect your app" tile shows), never in the
 * demo org, not dismissed, and until all four steps are done. Unknown values hide it.
 */
export function checklistVisible({
  hasTraces,
  isDemoOrg,
  dismissed,
  activation,
}: {
  hasTraces: boolean | undefined;
  isDemoOrg: boolean;
  dismissed: boolean;
  activation: Activation | undefined;
}): boolean {
  return (
    hasTraces === true &&
    !isDemoOrg &&
    !dismissed &&
    activation !== undefined &&
    doneCount(activation) < ACTIVATION_STEPS.length
  );
}
