import { describe, expect, it } from "vitest";

import { ONBOARDING_COPY } from "./copy";

function wordCount(strings: readonly string[]): number {
  return strings.join(" ").split(/\s+/).filter(Boolean).length;
}

describe("ONBOARDING_COPY", () => {
  it.each(Object.entries(ONBOARDING_COPY))("%s step's prose is at most 30 words", (_, step) => {
    const strings = [
      step.heroDescription ?? "",
      step.cardTitle,
      step.cardDescription,
      ...(step.helper ?? []),
    ];
    expect(wordCount(strings)).toBeLessThanOrEqual(30);
  });

  it("copy matches the spec", () => {
    expect(ONBOARDING_COPY).toEqual({
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
        helper: [
          "Waiting for an application trace…",
          "Verify ingestion with a test trace, then instrument your app.",
        ],
      },
    });
  });
});
