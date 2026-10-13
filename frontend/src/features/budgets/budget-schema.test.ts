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
    expect(budgetFieldOf("amount_usd", "project")).toBe("amount");
  });
});
