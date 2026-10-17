/**
 * Types mirroring the backend API's request and response bodies; clarifications are in
 * docs/api-deviations.md.
 * Field names are snake_case to match the wire format exactly; money is a
 * decimal string and every "unknown" value is `null`, never zero.
 */

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export interface ProblemFieldError {
  field: string;
  message: string;
  /** An uploaded file's error only: the row as a spreadsheet numbers it (0 = the whole file). */
  row?: number;
  /** An uploaded file's error only: the column at fault, or null when no one column is. */
  column?: string | null;
}

export interface ProblemDetails {
  type?: string;
  title?: string;
  status?: number;
  detail?: string;
  code?: string;
  request_id?: string;
  errors?: ProblemFieldError[];
  /** An RFC 9457 extension member of `409 ROUTE_VERSION_CONFLICT`. */
  current_version?: number;
  /** An RFC 9457 extension member of `502 EVALUATOR_FAILED`: the run's error code. */
  error?: string;
  /**
   * Extension members of `409 REPLAY_BUDGET_EXCEEDED`: the month's settled and reserved replay
   * spend and the budget (decimal strings), and the UTC month's start.
   */
  spent_usd?: string;
  reserved_usd?: string;
  budget_usd?: string;
  month_start?: string;
}

export type * from "./identity-types";
export type * from "./telemetry-types";
export type * from "./gateway-types";
export type * from "./alerts-types";
export type * from "./insights-types";
export type * from "./analytics-types";
export type * from "./providers-types";
