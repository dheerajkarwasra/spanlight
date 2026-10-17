import { describe, expect, it } from "vitest";

import type { Budget } from "@/lib/api";

import {
  EMPTY_BUDGET_VALUES,
  amountProblem,
  budgetFieldOf,
  budgetFormSchema,
  displayAmount,
  toBudgetInput,
  toBudgetUpdate,
  valuesFromBudget,
  type BudgetFormValues,
} from "./budget-schema";

// The server's rules (`app/budgets/schemas.py`): amount_usd is a Decimal > 0 with at most 14
// digits and 4 decimals; scope_id is null for a project budget and required for every other scope.

function form(overrides: Partial<BudgetFormValues>): BudgetFormValues {
  return { ...EMPTY_BUDGET_VALUES, name: "Monthly cap", amount: "50", ...overrides };
}

function issuePaths(values: BudgetFormValues): string[] {
  const result = budgetFormSchema.safeParse(values);
  return result.success ? [] : result.error.issues.map((issue) => issue.path.join("."));
}

function budget(overrides: Partial<Budget>): Budget {
  return {
    id: "b1",
    project_id: "p1",
    name: "Monthly cap",
    scope: "project",
    scope_id: null,
    period: "monthly",
    amount_usd: "50.0000",
    action: "notify",
    enabled: true,
    channel_ids: [],
    state: null,
    created_by: null,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    ...overrides,
  };
}

describe("amountProblem", () => {
  it("accepts what the server's numeric(14,4) above 0 accepts", () => {
    for (const amount of ["50", "50.", ".5", "0.0001", "9999999999.9999", "0000000000050"]) {
      expect(amountProblem(amount)).toBeNull();
    }
  });

  it("refuses 0, blanks and anything that is not a plain decimal", () => {
    for (const amount of ["", ".", "0", "0.0000", "-5", "1e3", "$50", "1,000", "5.5.5"]) {
      expect(amountProblem(amount)).not.toBeNull();
    }
  });

  it("refuses a fifth decimal and an 11th whole digit", () => {
    expect(amountProblem("0.00001")).toBe("Use at most 4 decimal places.");
    expect(amountProblem("10000000000")).toBe("Use an amount below $10,000,000,000.");
  });
});

describe("budgetFormSchema", () => {
  it("needs a scope value for every scope but project", () => {
    expect(issuePaths(form({ scope: "project" }))).toEqual([]);
    expect(issuePaths(form({ scope: "gateway_key" }))).toEqual(["gatewayKeyId"]);
    expect(issuePaths(form({ scope: "user", userId: "   " }))).toEqual(["userId"]);
    expect(issuePaths(form({ scope: "model", model: "gpt-4.1" }))).toEqual([]);
  });

  it("bounds the name to 1..80 characters after trimming", () => {
    expect(issuePaths(form({ name: "  " }))).toEqual(["name"]);
    expect(issuePaths(form({ name: "n".repeat(81) }))).toEqual(["name"]);
  });

  it("allows at most 10 channels", () => {
    const eleven = Array.from({ length: 11 }, (_, index) => `c${String(index)}`);
    expect(issuePaths(form({ channelIds: eleven }))).toEqual(["channelIds"]);
  });

  it("needs a valid key and a value for a metadata budget, and a name for a trace name one", () => {
    expect(issuePaths(form({ scope: "metadata" }))).toEqual(["metadataKey", "metadataValue"]);
    expect(
      issuePaths(form({ scope: "metadata", metadataKey: "a.b-c_1", metadataValue: "x" })),
    ).toEqual([]);
    expect(
      issuePaths(form({ scope: "metadata", metadataKey: "Customer ID", metadataValue: "acme" })),
    ).toEqual(["metadataKey"]);
    expect(
      issuePaths(form({ scope: "metadata", metadataKey: "k".repeat(65), metadataValue: "acme" })),
    ).toEqual(["metadataKey"]);
    expect(issuePaths(form({ scope: "trace_name", traceName: "   " }))).toEqual(["traceName"]);
    expect(issuePaths(form({ scope: "trace_name", traceName: "😀".repeat(256) }))).toEqual([]);
    expect(issuePaths(form({ scope: "trace_name", traceName: "t".repeat(257) }))).toEqual([
      "traceName",
    ]);
  });

  it("counts a metadata value in code points, up to 256", () => {
    // Two UTF-16 units each: a UTF-16 length check would refuse 256 of them.
    const value = "😀".repeat(256);
    expect(
      issuePaths(form({ scope: "metadata", metadataKey: "region", metadataValue: value })),
    ).toEqual([]);
    expect(
      issuePaths(form({ scope: "metadata", metadataKey: "region", metadataValue: `${value}😀` })),
    ).toEqual(["metadataValue"]);
  });

  it("checks the amount after trimming spaces", () => {
    expect(issuePaths(form({ amount: " 25.5 " }))).toEqual([]);
  });
});

describe("toBudgetInput", () => {
  it("sends a project budget with a null scope_id and a normalised amount", () => {
    expect(toBudgetInput(form({ amount: ".5", userId: "left-over" }))).toMatchObject({
      scope: "project",
      scope_id: null,
      amount_usd: "0.5",
    });
    expect(toBudgetInput(form({ amount: "50." })).amount_usd).toBe("50");
  });

  it("sends only the value of the chosen scope", () => {
    const values = form({ scope: "user", userId: "u-42", model: "gpt-4.1", gatewayKeyId: "k1" });
    expect(toBudgetInput(values)).toMatchObject({ scope: "user", scope_id: "u-42" });
  });
});

describe("metadata and trace name scopes", () => {
  it("composes key=value, keeping the value as typed", () => {
    const values = form({ scope: "metadata", metadataKey: "customer_id", metadataValue: "acme" });
    expect(toBudgetInput(values)).toMatchObject({
      scope: "metadata",
      scope_id: "customer_id=acme",
    });
    expect(
      toBudgetInput(form({ scope: "metadata", metadataKey: "k", metadataValue: " a" })).scope_id,
    ).toBe("k= a");
  });

  it("splits a stored scope at the first =", () => {
    const values = valuesFromBudget(
      budget({ scope: "metadata", scope_id: "customer_id=acme=prod" }),
    );
    expect(values).toMatchObject({ metadataKey: "customer_id", metadataValue: "acme=prod" });
    expect(
      toBudgetUpdate(
        { ...values, amount: "50.00" },
        budget({ scope: "metadata", scope_id: "customer_id=acme=prod" }),
      ),
    ).toEqual({});
  });

  it("sends the trace name as the scope", () => {
    const values = form({ scope: "trace_name", traceName: "summarize-thread" });
    expect(toBudgetInput(values)).toMatchObject({
      scope: "trace_name",
      scope_id: "summarize-thread",
    });
    expect(
      valuesFromBudget(budget({ scope: "trace_name", scope_id: "summarize-thread" })).traceName,
    ).toBe("summarize-thread");
  });
});

describe("toBudgetUpdate", () => {
  it("sends nothing for an untouched edit, despite the stored 4-decimal amount", () => {
    const stored = budget({ amount_usd: "50.0000" });
    expect(displayAmount(stored.amount_usd)).toBe("50.00");
    expect(toBudgetUpdate(form({ amount: "50.00" }), stored)).toEqual({});
  });

  it("sends scope and scope_id together when either changes", () => {
    const stored = budget({ scope: "user", scope_id: "u-1" });
    expect(toBudgetUpdate(form({ scope: "user", userId: "u-2", amount: "50.00" }), stored)).toEqual(
      {
        scope: "user",
        scope_id: "u-2",
      },
    );
    expect(
      toBudgetUpdate(form({ scope: "project", userId: "u-1", amount: "50.00" }), stored),
    ).toEqual({
      scope: "project",
      scope_id: null,
    });
  });

  it("keeps sub-cent amounts exact when loading a stored budget", () => {
    expect(displayAmount("0.1250")).toBe("0.125");
    expect(displayAmount("0.0001")).toBe("0.0001");
  });
});

describe("budgetFieldOf", () => {
  it("puts a scope_id error on the field of the current scope", () => {
    expect(budgetFieldOf("scope_id", "gateway_key")).toBe("gatewayKeyId");
    expect(budgetFieldOf("scope_id", "project")).toBeNull();
    expect(budgetFieldOf("scope_id", "metadata")).toBe("metadataKey");
    expect(budgetFieldOf("scope_id", "trace_name")).toBe("traceName");
    expect(budgetFieldOf("amount_usd", "project")).toBe("amount");
  });
});
