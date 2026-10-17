import type { ProblemDetails, ProblemFieldError } from "./types";

/** A failed API call, parsed from an RFC 9457 problem+json body when available. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly title: string;
  readonly detail: string | null;
  readonly requestId: string | null;
  readonly fieldErrors: ProblemFieldError[];
  /** `409 ROUTE_VERSION_CONFLICT` only: the version the route is at now. Null on every other error. */
  readonly currentVersion: number | null;
  /** `502 EVALUATOR_FAILED` only: why the run failed, e.g. `verdict_unparseable`. Null otherwise. */
  readonly runError: string | null;
  /** Seconds to wait before retrying, from the `Retry-After` header (429, 503). Null without one. */
  readonly retryAfterSeconds: number | null;

  constructor(status: number, problem: ProblemDetails, retryAfterSeconds: number | null = null) {
    const title = problem.title ?? defaultTitle(status);
    super(problem.detail ?? title);
    this.name = "ApiError";
    this.status = status;
    this.code = problem.code ?? `HTTP_${status}`;
    this.title = title;
    this.detail = problem.detail ?? null;
    this.requestId = problem.request_id ?? null;
    this.fieldErrors = problem.errors ?? [];
    this.currentVersion = problem.current_version ?? null;
    this.runError = typeof problem.error === "string" ? problem.error : null;
    this.retryAfterSeconds = retryAfterSeconds;
  }

  get isNotFound(): boolean {
    return this.status === 404;
  }

  get isUnauthorized(): boolean {
    return this.status === 401;
  }
}

/**
 * `422 EXPORT_TOO_LARGE`: more events match the audit log filters than one CSV may hold, so nothing
 * was sent. Narrow the filters. It is an `ApiError`, so `code`, `detail` and `requestId` are the
 * server's; the type only lets a caller tell this case apart from any other failure.
 */
export class ExportTooLargeError extends ApiError {
  constructor(source: ApiError) {
    super(source.status, {
      title: source.title,
      detail: source.detail ?? undefined,
      code: source.code,
      request_id: source.requestId ?? undefined,
      errors: source.fieldErrors,
    });
    this.name = "ExportTooLargeError";
  }
}

/**
 * The `Retry-After` header as whole seconds from `now`: either a delay in seconds or an HTTP date
 * (rounded up). Null when the header is missing or unreadable.
 */
export function parseRetryAfter(header: string | null, now: number): number | null {
  if (header === null) {
    return null;
  }
  const value = header.trim();
  if (/^\d+$/.test(value)) {
    return Number(value);
  }
  const at = Date.parse(value);
  if (Number.isNaN(at) || !/[a-z]/i.test(value)) {
    return null;
  }
  return Math.max(0, Math.ceil((at - now) / 1000));
}

/** Thrown when the request never produced an HTTP response (offline, DNS, CORS, abort). */
export class NetworkError extends Error {
  constructor(cause: unknown) {
    super("Can't reach the server. Check your connection and try again.", { cause });
    this.name = "NetworkError";
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

/**
 * `403 TWO_FACTOR_REQUIRED`: the organization requires two-factor authentication and the caller has
 * not turned it on. Every organization and project route answers it until they do; `/auth/*` stays
 * open so they can.
 */
export function isTwoFactorRequired(error: unknown): error is ApiError {
  return error instanceof ApiError && error.status === 403 && error.code === "TWO_FACTOR_REQUIRED";
}

/** A short, human-readable message for any error thrown by the API layer. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError || error instanceof NetworkError) {
    return error.message;
  }
  return "Something went wrong. Try again.";
}

function defaultTitle(status: number): string {
  if (status === 401) return "Not signed in";
  if (status === 403) return "Not allowed";
  if (status === 404) return "Not found";
  if (status === 409) return "Conflict";
  if (status === 422) return "Invalid request";
  if (status === 429) return "Too many requests";
  if (status >= 500) return "Server error";
  return "Request failed";
}
