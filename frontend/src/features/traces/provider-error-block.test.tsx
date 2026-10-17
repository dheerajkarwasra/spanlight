import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import type { Span } from "@/lib/api";

import { ProviderErrorBlock } from "./provider-error-block";

const failed: Span = {
  span_id: "span_1",
  parent_span_id: null,
  kind: "llm",
  name: "chat claude-sonnet-4-5",
  status: "error",
  status_message: "rate limited",
  error_class: "rate_limit",
  provider_error_type: "rate_limit_error",
  provider_error_code: null,
  rate_limit_kind: "tokens",
  started_at: "2026-10-15T10:00:00Z",
  ended_at: "2026-10-15T10:00:02Z",
  duration_ms: 2210,
  provider: "anthropic",
  model: "claude-sonnet-4-5",
  input_tokens: null,
  output_tokens: null,
  cached_tokens: null,
  cache_write_tokens: null,
  cache_write_1h_tokens: null,
  service_tier: null,
  web_search_requests: null,
  cost_usd: null,
  fee_usd: null,
  pricing_version: null,
  time_to_first_token_ms: null,
  input: null,
  output: null,
  attributes: {
    "provider.request_id": "req_011CUa7Kq2v9XYZ5tGhR3bLm",
    "provider.retry_after_s": 12,
    "provider.ratelimit": {
      "retry-after": "12",
      "anthropic-ratelimit-tokens-remaining": "0",
    },
    "spanlight.gateway.status": 429,
    "spanlight.gateway.attempts": 3,
    "spanlight.gateway.attempt_log": [
      {
        target: 0,
        name: "anthropic-prod",
        status: 429,
        error: null,
        duration_ms: 812,
        retry_after_s: 2,
      },
      {
        target: 0,
        name: "anthropic-prod",
        status: 429,
        error: null,
        duration_ms: 655,
        retry_after_s: 12,
      },
      {
        target: 1,
        name: "anthropic-eu",
        status: null,
        error: "timeout",
        duration_ms: 702,
        retry_after_s: null,
      },
    ],
  },
  truncated: false,
  finish_reason: null,
  prompt_version_id: null,
  usage_rollup: false,
  usage_state: "usage_not_reported",
};

function renderBlock(span: Span) {
  render(
    <TooltipProvider>
      <ProviderErrorBlock span={span} />
    </TooltipProvider>,
  );
}

function field(label: string): HTMLElement {
  const term = screen.getByText(label, { selector: "dt" });
  const value = term.nextElementSibling;
  if (!(value instanceof HTMLElement)) {
    throw new Error(`no value for ${label}`);
  }
  return value;
}

describe("ProviderErrorBlock", () => {
  it("shows the provider's verdict: type, code, kind, retry-after, request id and status", () => {
    renderBlock(failed);
    const block = screen.getByRole("region", { name: "Provider error" });
    expect(within(block).getByText("Rate limited")).toBeInTheDocument();
    expect(field("Type")).toHaveTextContent("rate_limit_error");
    expect(within(field("Code")).getByLabelText(/error code was not sent/)).toHaveTextContent("—");
    expect(field("Kind")).toHaveTextContent("Token limit · tokens");
    expect(field("Retry-after")).toHaveTextContent("12 s");
    expect(field("Request id")).toHaveTextContent("req_011CUa7Kq2v9XYZ5tGhR3bLm");
    expect(screen.getByRole("button", { name: "Copy request id" })).toBeInTheDocument();
    expect(field("Gateway answered")).toHaveTextContent("429");
  });

  it("lists the rate-limit headers as text once expanded", async () => {
    renderBlock(failed);
    const toggle = screen.getByRole("button", { name: /Rate-limit headers · 2/ });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    const headers = screen.getByRole("list", { name: "Rate-limit headers" });
    expect(within(headers).getByText("anthropic-ratelimit-tokens-remaining")).toBeInTheDocument();
    expect(within(headers).getByText("0")).toBeInTheDocument();
    await userEvent.click(toggle);
    expect(screen.queryByRole("list", { name: "Rate-limit headers" })).not.toBeInTheDocument();
  });

  it("shows the attempt log as a table", () => {
    renderBlock(failed);
    expect(screen.getByText("Attempts · 3")).toBeInTheDocument();
    const table = screen.getByRole("table", { name: "Attempts" });
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(4);
    expect(rows[3]).toHaveTextContent("3anthropic-eutimeout702 ms—");
  });

  it("marks the attempts the log did not keep", () => {
    const log = Array.from({ length: 10 }, () => ({
      target: 0,
      name: "anthropic-prod",
      status: 429,
      error: null,
      duration_ms: 500,
      retry_after_s: 1,
    }));
    renderBlock({
      ...failed,
      attributes: { "spanlight.gateway.attempt_log": log, "spanlight.gateway.attempts": 14 },
    });
    expect(screen.getByText("Attempts · 14 (first 9 and the last shown)")).toBeInTheDocument();
    expect(screen.getByText("… attempts 10 to 13 not stored")).toBeInTheDocument();
  });

  it("renders no table and does not crash on a malformed attempt log", () => {
    renderBlock({
      ...failed,
      attributes: { "spanlight.gateway.attempt_log": "not a list", "provider.ratelimit": "x" },
    });
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(
      screen.getByText("No rate-limit headers were captured for this span."),
    ).toBeInTheDocument();
  });

  it("shows every unknown as a dash with a reason for an OTLP span with a status only", () => {
    renderBlock({
      ...failed,
      provider_error_type: null,
      rate_limit_kind: "unknown",
      attributes: {},
    });
    expect(within(field("Type")).getByLabelText(/error type was not sent/)).toHaveTextContent("—");
    expect(field("Kind")).toHaveTextContent("Unknown · unknown");
    expect(within(field("Retry-after")).getByLabelText(/retry-after/)).toHaveTextContent("—");
    expect(within(field("Request id")).getByLabelText(/request id/)).toHaveTextContent("—");
    expect(screen.queryByText("Gateway answered")).not.toBeInTheDocument();
  });

  it("titles a gateway-made failure Gateway error and shows the gateway's code", () => {
    renderBlock({
      ...failed,
      error_class: "rate_limit",
      provider_error_type: null,
      rate_limit_kind: "gateway",
      attributes: {
        "spanlight.gateway.error_code": "RATE_LIMITED",
        "spanlight.gateway.retry_after_s": 3,
        "spanlight.gateway.status": 429,
      },
    });
    expect(screen.getByRole("region", { name: "Gateway error" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Provider error" })).not.toBeInTheDocument();
    expect(field("Gateway code")).toHaveTextContent("RATE_LIMITED");
    expect(field("Kind")).toHaveTextContent("Gateway key limit · gateway");
    expect(field("Retry-after")).toHaveTextContent("3 s");
    const type = within(field("Type")).getByLabelText(/not sent with this span/);
    expect(type).toHaveTextContent("—");
    expect(screen.queryByLabelText(/provider’s/)).not.toBeInTheDocument();
  });

  it("shows no Gateway code row for a provider's failure", () => {
    renderBlock(failed);
    expect(screen.queryByText("Gateway code")).not.toBeInTheDocument();
  });
});
