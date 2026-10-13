import { describe, expect, it } from "vitest";

import {
  buildSlugMap,
  orgIdFromParam,
  orgIdOfPath,
  orgSlug,
  projectIdFromParam,
  projectSlug,
  workspacePathFor,
} from "./slug-map";

const ACME = "0b6f6c2e-6a35-4c34-9a5a-1f0d9c1d0a01";
const GLOBEX = "0b6f6c2e-6a35-4c34-9a5a-1f0d9c1d0a02";
const ACME_BOT = "5d7f3f7e-2b8f-4d34-8f64-6c1f0e2a0b01";
const GLOBEX_BOT = "5d7f3f7e-2b8f-4d34-8f64-6c1f0e2a0b02";
const UNKNOWN_UUID = "9a9a9a9a-0000-4000-8000-000000000000";

const map = buildSlugMap(
  [
    { id: ACME, slug: "acme" },
    { id: GLOBEX, slug: "globex" },
  ],
  [
    [ACME, [{ id: ACME_BOT, slug: "support-bot" }]],
    [GLOBEX, [{ id: GLOBEX_BOT, slug: "support-bot" }]],
  ],
);

describe("orgIdFromParam", () => {
  it("turns an org slug into its id", () => {
    expect(orgIdFromParam(map, "acme")).toBe(ACME);
  });

  it("passes a known id through", () => {
    expect(orgIdFromParam(map, GLOBEX)).toBe(GLOBEX);
  });

  it("passes an unknown uuid through, so the server decides", () => {
    expect(orgIdFromParam(map, UNKNOWN_UUID)).toBe(UNKNOWN_UUID);
  });

  it("has no id for an unknown slug", () => {
    expect(orgIdFromParam(map, "initech")).toBeUndefined();
  });
});

describe("projectIdFromParam", () => {
  it("turns a project slug into its id", () => {
    expect(projectIdFromParam(map, ACME, "support-bot")).toBe(ACME_BOT);
  });

  it("resolves the same slug by org", () => {
    expect(projectIdFromParam(map, GLOBEX, "support-bot")).toBe(GLOBEX_BOT);
  });

  it("passes a uuid through", () => {
    expect(projectIdFromParam(map, ACME, ACME_BOT)).toBe(ACME_BOT);
    expect(projectIdFromParam(map, ACME, UNKNOWN_UUID)).toBe(UNKNOWN_UUID);
  });

  it("has no id for an unknown slug, or a slug of an org whose projects are not loaded", () => {
    expect(projectIdFromParam(map, ACME, "checkout")).toBeUndefined();
    expect(projectIdFromParam(map, UNKNOWN_UUID, "support-bot")).toBeUndefined();
  });
});

describe("orgSlug and projectSlug", () => {
  it("turn ids into slugs", () => {
    expect(orgSlug(map, GLOBEX)).toBe("globex");
    expect(projectSlug(map, GLOBEX, GLOBEX_BOT)).toBe("support-bot");
  });

  it("fall back to the id when it is unknown", () => {
    expect(orgSlug(map, UNKNOWN_UUID)).toBe(UNKNOWN_UUID);
    expect(projectSlug(map, ACME, UNKNOWN_UUID)).toBe(UNKNOWN_UUID);
    expect(projectSlug(map, ACME, GLOBEX_BOT)).toBe(GLOBEX_BOT);
  });

  it("are idempotent on a value that is already a slug", () => {
    expect(orgSlug(map, "acme")).toBe("acme");
    expect(projectSlug(map, ACME, "support-bot")).toBe("support-bot");
  });

  it("keep the id for an org whose slug is a reserved first path segment", () => {
    const reserved = buildSlugMap([{ id: ACME, slug: "api" }], []);
    expect(orgSlug(reserved, ACME)).toBe(ACME);
  });
});

describe("workspacePathFor", () => {
  it("is canonical for a slug path", () => {
    expect(workspacePathFor(map, "/acme/support-bot/traces")).toEqual({
      kind: "canonical",
      orgId: ACME,
      projectId: ACME_BOT,
    });
  });

  it("rewrites an id path to slugs and keeps the rest of the path", () => {
    expect(workspacePathFor(map, `/${GLOBEX}/${GLOBEX_BOT}/traces/abc`)).toEqual({
      kind: "redirect",
      orgId: GLOBEX,
      projectId: GLOBEX_BOT,
      pathname: "/globex/support-bot/traces/abc",
    });
  });

  it("rewrites a project index path", () => {
    expect(workspacePathFor(map, `/${ACME}/${ACME_BOT}`)).toMatchObject({
      kind: "redirect",
      pathname: "/acme/support-bot",
    });
  });

  it("is unknown when either slug does not resolve", () => {
    expect(workspacePathFor(map, "/initech/support-bot/traces")).toEqual({ kind: "unknown" });
    expect(workspacePathFor(map, "/acme/nope/traces")).toEqual({ kind: "unknown" });
  });
});

describe("orgIdOfPath", () => {
  it("reads the org from the first segment, slug or id", () => {
    expect(orgIdOfPath(map, "/globex/anything/traces")).toBe(GLOBEX);
    expect(orgIdOfPath(map, `/${ACME}/x`)).toBe(ACME);
  });

  it("has no org for an unknown slug", () => {
    expect(orgIdOfPath(map, "/initech/support-bot")).toBeUndefined();
  });
});
