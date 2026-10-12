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
}

export type * from "./identity-types";
export type * from "./telemetry-types";
export type * from "./gateway-types";
export type * from "./alerts-types";
export type * from "./insights-types";
export type * from "./analytics-types";
