import { describe, expect, it } from "vitest";

import {
  activeFiltersOf,
  normaliseFilterPatch,
  onlyRetriesFilter,
  removalPatch,
} from "./use-trace-filters";

describe("activeFiltersOf", () => {
  it("lists the environment first, then facets in a stable order", () => {
    expect(
      activeFiltersOf({ q: "refund", model: "gpt-4.1-mini", env: "production", status: "error" }),
    ).toEqual([
      { key: "env", label: "Environment", value: "production" },
      { key: "status", label: "Status", value: "Error" },
      { key: "model", label: "Model", value: "gpt-4.1-mini" },
      { key: "q", label: "Search", value: "refund" },
    ]);
  });

  it("skips empty values and ignores the time range", () => {
    expect(activeFiltersOf({ range: "7d", tag: "", user: "u_8f21" })).toEqual([
      { key: "user", label: "User", value: "u_8f21" },
    ]);
  });
});

describe("the retries filter", () => {
  it("shows as a removable chip, and the sort does not", () => {
    const active = activeFiltersOf({ has_retries: true, sort: "retry_cost_usd" });
    expect(active).toEqual([{ key: "has_retries", label: "Retries", value: "has retries" }]);
    expect(removalPatch("has_retries")).toEqual({ has_retries: undefined });
  });

  it("is the only filter when nothing but the environment is on beside it", () => {
    expect(onlyRetriesFilter(activeFiltersOf({ has_retries: true, env: "production" }))).toBe(true);
    expect(onlyRetriesFilter(activeFiltersOf({ has_retries: true, status: "error" }))).toBe(false);
    expect(onlyRetriesFilter(activeFiltersOf({ status: "error" }))).toBe(false);
  });
});

describe("the metadata and trace name filters", () => {
  it("shows a key and value as one pair chip", () => {
    expect(activeFiltersOf({ metadata_key: "customer_id", metadata_value: "acme" })).toEqual([
      {
        key: "metadata",
        label: "Metadata",
        value: "customer_id = acme",
        pair: { key: "customer_id", value: "acme" },
      },
    ]);
  });

  it("shows a key alone as the missing-key chip", () => {
    expect(activeFiltersOf({ metadata_key: "customer_id" })).toEqual([
      {
        key: "metadata",
        label: "Metadata",
        value: "No customer_id",
        pair: { key: "customer_id", value: undefined },
      },
    ]);
  });

  it("ignores a value without a key, which the list does not send", () => {
    expect(activeFiltersOf({ metadata_value: "acme" })).toEqual([]);
  });

  it("removes the key and the value together", () => {
    expect(removalPatch("metadata")).toEqual({
      metadata_key: undefined,
      metadata_value: undefined,
    });
  });

  it("shows the exact trace name as its own chip", () => {
    expect(activeFiltersOf({ name: "summarize-thread" })).toEqual([
      { key: "name", label: "Trace name", value: "summarize-thread" },
    ]);
    expect(removalPatch("name")).toEqual({ name: undefined });
  });
});

describe("normaliseFilterPatch", () => {
  it("clears the value with a blank key", () => {
    expect(normaliseFilterPatch({ metadata_key: " ", metadata_value: "acme" })).toEqual({
      metadata_key: undefined,
      metadata_value: undefined,
    });
    expect(normaliseFilterPatch({ metadata_key: undefined, metadata_value: "acme" })).toEqual({
      metadata_key: undefined,
      metadata_value: undefined,
    });
  });

  it("turns an empty value into the missing-key filter and keeps spaces in a value", () => {
    expect(normaliseFilterPatch({ metadata_key: "a.b-c", metadata_value: "" })).toEqual({
      metadata_key: "a.b-c",
      metadata_value: undefined,
    });
    expect(normaliseFilterPatch({ metadata_key: "a.b-c", metadata_value: " acme " })).toEqual({
      metadata_key: "a.b-c",
      metadata_value: " acme ",
    });
  });

  it("clears a blank facet and leaves the metadata params alone when absent", () => {
    expect(normaliseFilterPatch({ user: "  ", name: "summarize" })).toEqual({
      user: undefined,
      name: "summarize",
    });
  });
});
