import type { UseFormSetError } from "react-hook-form";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";

import { applyMappedFieldErrors, applyServerFieldErrors } from "./form-errors";

interface Form {
  baseUrl: string;
  allowedModels: string[];
  name: string;
}

function problem(errors: { field: string; message: string }[]): ApiError {
  return new ApiError(422, { code: "VALIDATION_ERROR", title: "Invalid", errors });
}

function spy() {
  return vi.fn() as unknown as UseFormSetError<Form> & ReturnType<typeof vi.fn>;
}

describe("applyMappedFieldErrors", () => {
  it("maps API field names, by their first path segment, onto form fields", () => {
    const setError = spy();
    const applied = applyMappedFieldErrors<Form>(
      problem([
        { field: "base_url", message: "must be https" },
        { field: "allowed_models.3", message: "unknown model" },
      ]),
      setError,
      { base_url: "baseUrl", allowed_models: "allowedModels" },
    );
    expect(applied).toBe(true);
    expect(setError).toHaveBeenCalledWith("baseUrl", { type: "server", message: "must be https" });
    expect(setError).toHaveBeenCalledWith("allowedModels", {
      type: "server",
      message: "unknown model",
    });
  });

  it("leaves a field the form does not have to the form-level message", () => {
    const setError = spy();
    expect(
      applyMappedFieldErrors<Form>(
        problem([{ field: "provider", message: "x" }]),
        setError,
        () => null,
      ),
    ).toBe(false);
    expect(setError).not.toHaveBeenCalled();
  });

  it("ignores anything that is not an API problem", () => {
    const setError = spy();
    expect(applyMappedFieldErrors<Form>(new Error("offline"), setError, {})).toBe(false);
    expect(setError).not.toHaveBeenCalled();
  });
});

describe("applyServerFieldErrors", () => {
  it("attaches only errors whose name matches a listed field exactly", () => {
    const setError = spy();
    const applied = applyServerFieldErrors<Form>(
      problem([
        { field: "name", message: "taken" },
        { field: "name.extra", message: "nested" },
      ]),
      setError,
      ["name"],
    );
    expect(applied).toBe(true);
    expect(setError).toHaveBeenCalledTimes(1);
    expect(setError).toHaveBeenCalledWith("name", { type: "server", message: "taken" });
  });
});
