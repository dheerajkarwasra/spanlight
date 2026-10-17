import { describe, expect, it } from "vitest";

import { ApiError, type BillingSource } from "@/lib/api";

import {
  emptySourceForm,
  NOT_CONFIGURED_FALLBACK,
  notConfiguredCopy,
  sourceErrorsOf,
  sourceFormOf,
  takenProjects,
  toSourceCreate,
  toSourceUpdate,
  validateSourceForm,
  type SourceFormValues,
} from "./source-form";

function values(overrides: Partial<SourceFormValues> = {}): SourceFormValues {
  return { ...emptySourceForm(), name: "Prod account", ...overrides };
}

function source(overrides: Partial<BillingSource> = {}): BillingSource {
  return {
    id: "src_1",
    kind: "anthropic_admin_api",
    provider: "anthropic",
    name: "Prod account",
    provider_scope: null,
    enabled: true,
    status: "ok",
    key_set: true,
    key_id: "v1",
    last_fetched_at: null,
    last_success_at: null,
    last_error: null,
    projects: [],
    created_by: null,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    ...overrides,
  };
}

describe("validateSourceForm", () => {
  it("accepts an Anthropic Admin API key and an OpenAI admin key by their prefixes", () => {
    expect(validateSourceForm(values({ adminKey: "sk-ant-admin01-abc" }))).toEqual({});
    expect(
      validateSourceForm(values({ kind: "openai_costs_api", adminKey: "sk-admin-abc" })),
    ).toEqual({});
  });

  it("refuses a key with the other provider's prefix or an ordinary API key", () => {
    expect(validateSourceForm(values({ adminKey: "sk-admin-abc" })).adminKey).toBe(
      "An Anthropic Admin API key starts with sk-ant-admin.",
    );
    expect(
      validateSourceForm(values({ kind: "openai_costs_api", adminKey: "sk-proj-abc" })).adminKey,
    ).toBe("An OpenAI admin key starts with sk-admin.");
    expect(validateSourceForm(values({ adminKey: "sk-ant-api03-abc" })).adminKey).toBe(
      "An Anthropic Admin API key starts with sk-ant-admin.",
    );
  });

  it("trims the key before checking it, and requires one for the API kinds", () => {
    expect(validateSourceForm(values({ adminKey: "  sk-ant-admin01-abc \n" }))).toEqual({});
    expect(validateSourceForm(values({ adminKey: "   " })).adminKey).toBe(
      "Paste the Admin API key.",
    );
  });

  it("refuses a key longer than 512 characters", () => {
    const adminKey = `sk-ant-admin${"x".repeat(501)}`;
    expect(validateSourceForm(values({ adminKey })).adminKey).toBe("Use 512 characters or fewer.");
  });

  it("needs no key for a CSV source but needs its provider", () => {
    expect(validateSourceForm(values({ kind: "csv_upload", provider: "" }))).toEqual({
      provider: "Choose the provider whose costs the file holds.",
    });
    expect(validateSourceForm(values({ kind: "csv_upload", provider: "google" }))).toEqual({});
  });

  it("checks no key when editing, since the key is changed by rotating it", () => {
    expect(validateSourceForm(values({ adminKey: "" }), "edit")).toEqual({});
  });

  it("requires a name of 1 to 128 characters after trimming", () => {
    const valid = { adminKey: "sk-ant-admin01-abc" };
    expect(validateSourceForm(values({ ...valid, name: "  " })).name).toBe(
      "Enter a name for this source.",
    );
    expect(validateSourceForm(values({ ...valid, name: "x".repeat(129) })).name).toBe(
      "Use 128 characters or fewer.",
    );
    expect(validateSourceForm(values({ ...valid, name: "x".repeat(128) }))).toEqual({});
  });

  it("limits the provider scope to 128 characters", () => {
    const form = values({ adminKey: "sk-ant-admin01-abc", providerScope: "w".repeat(129) });
    expect(validateSourceForm(form).providerScope).toBe("Use 128 characters or fewer.");
  });
});

describe("toSourceCreate", () => {
  it("sends the trimmed key and scope for an API kind, never a provider", () => {
    const body = toSourceCreate(
      values({ adminKey: " sk-ant-admin01-abc ", providerScope: " wrkspc_1 ", projectIds: ["p1"] }),
    );
    expect(body).toEqual({
      kind: "anthropic_admin_api",
      name: "Prod account",
      admin_key: "sk-ant-admin01-abc",
      provider_scope: "wrkspc_1",
      project_ids: ["p1"],
    });
  });

  it("sends the provider and no key for a CSV source", () => {
    const body = toSourceCreate(values({ kind: "csv_upload", provider: "google", adminKey: "x" }));
    expect(body).toEqual({
      kind: "csv_upload",
      name: "Prod account",
      provider: "google",
      project_ids: [],
    });
  });
});

describe("toSourceUpdate", () => {
  it("sends what changed, the whole project list, and null to clear the scope", () => {
    const stored = source({ provider_scope: "wrkspc_1" });
    const form = { ...sourceFormOf(stored), name: "Prod", providerScope: "", projectIds: ["p2"] };
    expect(toSourceUpdate(form, stored)).toEqual({
      name: "Prod",
      provider_scope: null,
      project_ids: ["p2"],
    });
  });

  it("sends nothing but the project list when nothing else changed", () => {
    const stored = source();
    expect(toSourceUpdate(sourceFormOf(stored), stored)).toEqual({ project_ids: [] });
  });
});

describe("takenProjects", () => {
  const sources = [
    source({ id: "a", name: "Staging account", projects: [{ id: "p1", name: "Docs", slug: "d" }] }),
    source({ id: "b", name: "OpenAI prod", provider: "openai", kind: "openai_costs_api" }),
    source({ id: "c", name: "Prod account", projects: [{ id: "p2", name: "Web", slug: "w" }] }),
  ];

  it("names the other source of the same provider that reconciles each project", () => {
    expect(takenProjects(sources, "anthropic", "c")).toEqual(new Map([["p1", "Staging account"]]));
  });

  it("ignores sources of other providers and is empty without a provider", () => {
    expect(takenProjects(sources, "openai", null)).toEqual(new Map());
    expect(takenProjects(sources, null, null)).toEqual(new Map());
  });
});

describe("sourceErrorsOf", () => {
  it("puts a taken name on the name field", () => {
    const error = new ApiError(409, {
      code: "BILLING_SOURCE_NAME_TAKEN",
      detail: "A billing source with this name already exists in this organization.",
    });
    expect(sourceErrorsOf(error)).toEqual({
      name: "A billing source with this name already exists in this organization.",
    });
  });

  it("puts a taken project on the projects field with the detail naming the other source", () => {
    const error = new ApiError(409, {
      code: "BILLING_SOURCE_PROJECT_TAKEN",
      detail:
        "A project is already compared with Staging account. A project is compared with one account per provider; remove it from Staging account first.",
      errors: [
        { field: "project_ids", message: "A project is already compared with Staging account." },
      ],
    });
    expect(sourceErrorsOf(error)).toEqual({
      projectIds:
        "A project is already compared with Staging account. A project is compared with one account per provider; remove it from Staging account first.",
    });
  });

  it("maps a 422's API field names to the form's fields", () => {
    const error = new ApiError(422, {
      code: "VALIDATION_ERROR",
      errors: [
        { field: "admin_key", message: "does not look like an Admin API key" },
        { field: "unknown_field", message: "ignored" },
      ],
    });
    expect(sourceErrorsOf(error)).toEqual({ adminKey: "does not look like an Admin API key" });
  });

  it("is null for an error no field explains", () => {
    expect(sourceErrorsOf(new ApiError(500, {}))).toBeNull();
    expect(sourceErrorsOf(new Error("boom"))).toBeNull();
  });
});

describe("notConfiguredCopy", () => {
  it("shows the server's own sentence, so the two never drift apart", () => {
    const detail =
      "Billing sources that fetch from a provider are encrypted with a server key that isn't set. Ask your administrator to set CREDENTIALS_KEYS, or add a CSV source.";
    const error = new ApiError(409, { code: "NOT_CONFIGURED", detail });
    expect(notConfiguredCopy(error)).toBe(detail);
  });

  it("falls back to its own sentence when the server sent none", () => {
    expect(notConfiguredCopy(new ApiError(409, { code: "NOT_CONFIGURED" }))).toBe(
      NOT_CONFIGURED_FALLBACK,
    );
    expect(notConfiguredCopy(new Error("offline"))).toBe(NOT_CONFIGURED_FALLBACK);
  });
});
