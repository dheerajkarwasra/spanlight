import { api } from "./client";
import { projectPath } from "./paths";
import type { Budget, BudgetBurn, BudgetInput, BudgetUpdate } from "./types";

function budgetsPath(projectId: string): string {
  return `${projectPath(projectId)}/budgets`;
}

function budgetPath(projectId: string, budgetId: string): string {
  return `${budgetsPath(projectId)}/${encodeURIComponent(budgetId)}`;
}

/**
 * A project's budgets. Reads need `project:read`, writes `alerts:write` (admin). Errors: `409
 * BUDGET_NAME_TAKEN`, `422 LIMIT_EXCEEDED` (50 per project), `422 UNKNOWN_SCOPE`, `422
 * UNKNOWN_CHANNEL`.
 */
export const budgetsApi = {
  list: (projectId: string): Promise<Budget[]> => api.get<Budget[]>(budgetsPath(projectId)),
  get: (projectId: string, budgetId: string): Promise<Budget> =>
    api.get<Budget>(budgetPath(projectId, budgetId)),
  /** Cumulative spend per day of the current period (session only, not API keys). */
  burn: (projectId: string, budgetId: string): Promise<BudgetBurn> =>
    api.get<BudgetBurn>(`${budgetPath(projectId, budgetId)}/burn`),
  /** `422 UNKNOWN_SCOPE` when a `gateway_key` scope names no key of the project. */
  create: (projectId: string, input: BudgetInput): Promise<Budget> =>
    api.post<Budget>(budgetsPath(projectId), input),
  update: (projectId: string, budgetId: string, update: BudgetUpdate): Promise<Budget> =>
    api.patch<Budget>(budgetPath(projectId, budgetId), update),
  delete: (projectId: string, budgetId: string): Promise<void> =>
    api.delete(budgetPath(projectId, budgetId)),
};
