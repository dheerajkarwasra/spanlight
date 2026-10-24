import type { OnboardingStepId } from "./steps";

interface StepCopy {
  heroDescription?: string;
  cardTitle: string;
  cardDescription: string;
  /** Status lines shown while waiting: title, then helper. */
  helper?: string[];
}

/**
 * The prose each setup step shows by default. Kept in one place so a test can hold every step
 * to 25 words; labels, buttons, code and conditional notices are not counted and live with
 * their components.
 */
export const ONBOARDING_COPY: Record<OnboardingStepId, StepCopy> = {
  org: {
    heroDescription: "An organization, a project, then your first trace.",
    cardTitle: "Create your organization",
    cardDescription: "Your company or team. Invite people later.",
  },
  project: {
    heroDescription: "Next, add the app you want to observe.",
    cardTitle: "Name your first project",
    cardDescription: "One per app or service. Environments live inside it.",
  },
  connect: {
    cardTitle: "API key",
    cardDescription: "Shown once. Revoke it in Settings › API keys.",
    helper: ["Waiting for your first trace…", "Send a test trace or run a snippet."],
  },
};
