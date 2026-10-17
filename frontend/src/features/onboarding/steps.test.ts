import { describe, expect, it } from "vitest";

import type { Membership, Project } from "@/lib/api";

import { resolveOnboardingState, stepIndex, stepStatus } from "./steps";

const membership: Membership = {
  org: { id: "org_1", name: "Acme", slug: "acme", is_demo: false, require_2fa: false },
  role: "owner",
};

const project: Project = {
  id: "prj_1",
  org_id: "org_1",
  name: "My app",
  slug: "my-app",
  retention_days: 30,
  capture_payloads: true,
  weekly_digest_enabled: true,
  insight_channel_ids: [],
  promoted_metadata_keys: [],
  created_at: "2026-10-07T00:00:00Z",
};

describe("resolveOnboardingState", () => {
  it("starts at the organization step", () => {
    expect(resolveOnboardingState({ search: {}, memberships: [], projects: undefined })).toEqual({
      step: "org",
      notice: null,
    });
  });

  it("falls back to step 1 when the org in the URL isn't one of the user's", () => {
    const state = resolveOnboardingState({
      search: { org: "org_other" },
      memberships: [membership],
      projects: undefined,
    });
    expect(state.step).toBe("org");
    expect(state).toHaveProperty("notice", expect.stringMatching(/access/));
  });

  it("goes to the project step for a known org", () => {
    const state = resolveOnboardingState({
      search: { org: "org_1" },
      memberships: [membership],
      projects: undefined,
    });
    expect(state).toEqual({ step: "project", membership, notice: null });
  });

  it("waits for the project list before verifying the project", () => {
    const state = resolveOnboardingState({
      search: { org: "org_1", project: "prj_1" },
      memberships: [membership],
      projects: undefined,
    });
    expect(state).toEqual({ step: "loading" });
  });

  it("falls back to step 2 when the project isn't in the org", () => {
    const state = resolveOnboardingState({
      search: { org: "org_1", project: "prj_missing" },
      memberships: [membership],
      projects: [project],
    });
    expect(state.step).toBe("project");
    expect(state).toHaveProperty("notice", expect.stringMatching(/doesn't exist/));
  });

  it("reaches the connect step with a verified org and project", () => {
    const state = resolveOnboardingState({
      search: { org: "org_1", project: "prj_1" },
      memberships: [membership],
      projects: [project],
    });
    expect(state).toEqual({ step: "connect", membership, project });
  });
});

describe("stepIndex", () => {
  it("orders the steps", () => {
    expect(stepIndex("org")).toBe(0);
    expect(stepIndex("project")).toBe(1);
    expect(stepIndex("connect")).toBe(2);
  });
});

describe("stepStatus", () => {
  it("marks earlier steps done, the current one current and later ones upcoming", () => {
    expect(stepStatus("org", "project")).toBe("done");
    expect(stepStatus("project", "project")).toBe("current");
    expect(stepStatus("connect", "project")).toBe("upcoming");
  });

  it("starts with only the first step current", () => {
    expect(stepStatus("org", "org")).toBe("current");
    expect(stepStatus("project", "org")).toBe("upcoming");
  });

  it("marks every step done once the first trace arrived", () => {
    expect(stepStatus("org", "complete")).toBe("done");
    expect(stepStatus("project", "complete")).toBe("done");
    expect(stepStatus("connect", "complete")).toBe("done");
  });
});
