import { describe, expect, it } from "vitest";

import type { Span } from "@/lib/api";

import {
  attemptRows,
  attemptTotal,
  formatSeconds,
  gatewayErrorCodeOf,
  isGatewayError,
  metadataAttributes,
  retryAfterSeconds,
  showsProviderErrorBlock,
  snapshotRows,
} from "./provider-error-format";

function entry(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    target: 0,
    name: "anthropic-prod",
    status: 429,
    error: null,
    duration_ms: 812.4,
    retry_after_s: 2,
    ...overrides,
  };
}

describe("attemptRows", () => {
  it("numbers the attempts in order and shows the status or the error kind", () => {
    const rows = attemptRows({
      "spanlight.gateway.attempt_log": [
        entry(),
        entry({ target: 1, name: "anthropic-eu", status: null, error: "timeout" }),
      ],
    });
    expect(rows).toEqual([
      {
        number: 1,
        target: 0,
        name: "anthropic-prod",
        result: "429",
        durationMs: 812.4,
        retryAfterS: 2,
      },
      {
        number: 2,
        target: 1,
        name: "anthropic-eu",
        result: "timeout",
        durationMs: 812.4,
        retryAfterS: 2,
      },
    ]);
  });

  it("gives the last kept entry the true attempt number when the log was cut to 10", () => {
    const log = Array.from({ length: 10 }, () => entry());
    const attributes = { "spanlight.gateway.attempt_log": log, "spanlight.gateway.attempts": 14 };
    const rows = attemptRows(attributes);
    expect(rows.map((row) => row.number)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 14]);
    expect(attemptTotal(attributes, rows)).toBe(14);
  });

  it("returns no rows for a missing or malformed log and skips malformed entries", () => {
    expect(attemptRows({})).toEqual([]);
    expect(attemptRows({ "spanlight.gateway.attempt_log": "[{}]" })).toEqual([]);
    expect(attemptRows({ "spanlight.gateway.attempt_log": { target: 0 } })).toEqual([]);
    const rows = attemptRows({
      "spanlight.gateway.attempt_log": [
        "junk",
        null,
        entry({ name: 7, status: "429", duration_ms: "fast", retry_after_s: null }),
      ],
    });
    expect(rows).toEqual([
      { number: 1, target: 0, name: null, result: null, durationMs: null, retryAfterS: null },
    ]);
  });

  it("never lets a bogus attempts count shrink the log", () => {
    const attributes = {
      "spanlight.gateway.attempt_log": [entry(), entry()],
      "spanlight.gateway.attempts": "lots",
    };
    expect(attemptTotal(attributes, attemptRows(attributes))).toBe(2);
  });
});

describe("snapshotRows", () => {
  it("lists retry-after first, then the headers by name, as text", () => {
    expect(
      snapshotRows({
        "provider.ratelimit": {
          "anthropic-ratelimit-requests-reset": "2026-10-16T05:27:42Z",
          "anthropic-ratelimit-requests-limit": "4000",
          "retry-after": "12",
          "anthropic-ratelimit-requests-remaining": 3987,
        },
      }),
    ).toEqual([
      ["retry-after", "12"],
      ["anthropic-ratelimit-requests-limit", "4000"],
      ["anthropic-ratelimit-requests-remaining", "3987"],
      ["anthropic-ratelimit-requests-reset", "2026-10-16T05:27:42Z"],
    ]);
  });

  it("drops nested values and tolerates a snapshot that is not an object", () => {
    expect(snapshotRows({ "provider.ratelimit": "retry-after: 12" })).toEqual([]);
    expect(snapshotRows({ "provider.ratelimit": ["retry-after"] })).toEqual([]);
    expect(snapshotRows({ "provider.ratelimit": { "retry-after": { s: 1 }, a: null } })).toEqual(
      [],
    );
  });
});

describe("formatSeconds", () => {
  it("keeps at most one decimal and never splits the unit from the number", () => {
    expect(formatSeconds(12)).toBe("12\u00a0s");
    expect(formatSeconds(2.25)).toBe("2.3\u00a0s");
  });
});

describe("retryAfterSeconds", () => {
  it("prefers the provider's value, then the gateway's, and ignores non-numbers", () => {
    expect(retryAfterSeconds({ "provider.retry_after_s": 12 })).toBe(12);
    expect(retryAfterSeconds({ "spanlight.gateway.retry_after_s": 2 })).toBe(2);
    expect(retryAfterSeconds({ "provider.retry_after_s": "12" })).toBeNull();
    expect(retryAfterSeconds({})).toBeNull();
  });
});

const span = {
  kind: "llm",
  status: "error",
  provider_error_type: "rate_limit_error",
  provider_error_code: null,
  attributes: {},
} as unknown as Span;

describe("showsProviderErrorBlock", () => {
  it("shows the block for a failed llm span and for a gateway span that retried", () => {
    expect(showsProviderErrorBlock(span)).toBe(true);
    expect(showsProviderErrorBlock({ ...span, kind: "tool" })).toBe(false);
    expect(showsProviderErrorBlock({ ...span, status: "ok" })).toBe(false);
    const retried = { "spanlight.gateway.attempt_log": [entry(), entry({ status: 200 })] };
    expect(showsProviderErrorBlock({ ...span, status: "ok", attributes: retried })).toBe(true);
    const single = { "spanlight.gateway.attempt_log": [entry({ status: 200 })] };
    expect(showsProviderErrorBlock({ ...span, status: "ok", attributes: single })).toBe(false);
  });
});

describe("isGatewayError", () => {
  it("is true when the gateway made the failure: its own code, class gateway or kind gateway", () => {
    const coded = { ...span, attributes: { "spanlight.gateway.error_code": "BUDGET_EXCEEDED" } };
    expect(gatewayErrorCodeOf(coded.attributes)).toBe("BUDGET_EXCEEDED");
    expect(isGatewayError(coded)).toBe(true);
    expect(isGatewayError({ ...span, error_class: "gateway" })).toBe(true);
    expect(isGatewayError({ ...span, rate_limit_kind: "gateway" })).toBe(true);
  });

  it("is false for a provider's failure, a blank code and a span that did not fail", () => {
    expect(isGatewayError({ ...span, error_class: "rate_limit", rate_limit_kind: "tokens" })).toBe(
      false,
    );
    expect(isGatewayError({ ...span, attributes: { "spanlight.gateway.error_code": "" } })).toBe(
      false,
    );
    expect(
      isGatewayError({ ...span, status: "ok", error_class: null, rate_limit_kind: "gateway" }),
    ).toBe(false);
  });
});

describe("metadataAttributes", () => {
  const attributes = {
    "provider.error.type": "rate_limit_error",
    "provider.request_id": "req_1",
    "provider.ratelimit": { "retry-after": "12" },
    "spanlight.gateway.attempt_log": [entry()],
    "spanlight.gateway.status": 429,
    "app.feature": "support",
  };

  it("hides the keys the block renders while the block is shown", () => {
    expect(metadataAttributes({ ...span, attributes })).toEqual({
      "spanlight.gateway.status": 429,
      "app.feature": "support",
    });
  });

  it("hides the gateway's code and its retry-after when the block shows them", () => {
    const refusal = {
      "spanlight.gateway.error_code": "RATE_LIMITED",
      "spanlight.gateway.retry_after_s": 3,
      "spanlight.gateway.status": 429,
    };
    expect(
      metadataAttributes({ ...span, rate_limit_kind: "gateway", attributes: refusal }),
    ).toEqual({ "spanlight.gateway.status": 429 });
  });

  it("keeps the gateway's retry-after when the provider's value is the one shown", () => {
    const both = { "provider.retry_after_s": 12, "spanlight.gateway.retry_after_s": 2 };
    expect(metadataAttributes({ ...span, attributes: both })).toEqual({
      "spanlight.gateway.retry_after_s": 2,
    });
  });

  it("keeps every key when there is no block", () => {
    expect(metadataAttributes({ ...span, status: "ok", attributes })).toEqual(attributes);
  });

  it("hides only the attempt log of a gateway span that succeeded after a retry", () => {
    const succeeded = {
      "provider.request_id": "req_2",
      "provider.ratelimit": { "x-ratelimit-remaining-tokens": "9000" },
      "spanlight.gateway.attempt_log": [entry(), entry({ status: 200 })],
    };
    expect(metadataAttributes({ ...span, status: "ok", attributes: succeeded })).toEqual({
      "provider.request_id": "req_2",
      "provider.ratelimit": { "x-ratelimit-remaining-tokens": "9000" },
    });
  });

  it("keeps a key the block could not read, since the block shows a dash for it", () => {
    const malformed = {
      "provider.error.code": "insufficient_quota",
      "provider.request_id": 42,
      "provider.retry_after_s": "soon",
      "provider.ratelimit": "retry-after: 12",
      "spanlight.gateway.attempt_log": "not a list",
    };
    // The span has no `provider_error_code`, so the block shows "—" and the raw key stays.
    expect(metadataAttributes({ ...span, attributes: malformed })).toEqual(malformed);
  });
});
