import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { WaitingForTrace } from "./first-trace-status";
import type { TestTraceIds } from "./test-trace";
import { TestTraceButton } from "./test-trace-button";

const IDS: TestTraceIds = { traceId: "c".repeat(32), spanId: "d".repeat(16) };

function ok() {
  return new Response(JSON.stringify({ accepted: 1, rejected: [] }), { status: 200 });
}

/** The ids of the span sent in the given fetch call. */
function sentIds(fetchMock: ReturnType<typeof vi.fn>, call: number) {
  const init = fetchMock.mock.calls[call]?.[1] as RequestInit;
  const { spans } = JSON.parse(init.body as string) as {
    spans: { trace_id: string; span_id: string }[];
  };
  return spans.map(({ trace_id, span_id }) => ({ trace_id, span_id }));
}

/** Holds `sent` the way ConnectStep does, and can hide the button like a tab switch. */
function Harness({
  apiKey = "k",
  onSent = vi.fn(),
}: {
  apiKey?: string | null;
  onSent?: () => void;
}) {
  const [sent, setSent] = useState(false);
  const [shown, setShown] = useState(true);
  return (
    <>
      <button type="button" onClick={() => setShown((value) => !value)}>
        Toggle
      </button>
      {shown ? (
        <TestTraceButton
          apiKey={apiKey}
          ids={IDS}
          sent={sent}
          environment="production"
          onSent={() => {
            setSent(true);
            onSent();
          }}
        />
      ) : null}
    </>
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("TestTraceButton", () => {
  it("posts one span with the key, then reports it sent and disables itself", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok());
    vi.stubGlobal("fetch", fetchMock);
    const onSent = vi.fn();
    render(<Harness apiKey="spl_live_a_b" onSent={onSent} />);

    await userEvent.click(screen.getByRole("button", { name: "Send a test trace" }));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = (fetchMock.mock.calls[0] ?? []) as [string, RequestInit];
    expect(url).toBe("/v1/traces");
    expect(init).toMatchObject({
      method: "POST",
      credentials: "omit",
      headers: { Authorization: "Bearer spl_live_a_b", "Content-Type": "application/json" },
    });
    expect(sentIds(fetchMock, 0)).toEqual([{ trace_id: IDS.traceId, span_id: IDS.spanId }]);
    expect(onSent).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Send a test trace" })).toBeDisabled();
  });

  it("shows the reason on error and reuses the same ids on retry", async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(ok());
    vi.stubGlobal("fetch", fetchMock);
    const onSent = vi.fn();
    render(<Harness onSent={onSent} />);

    await userEvent.click(screen.getByRole("button", { name: "Send a test trace" }));
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Couldn't send the test trace. Check your connection and try again.",
    );
    expect(onSent).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(sentIds(fetchMock, 1)).toEqual(sentIds(fetchMock, 0));
    expect(onSent).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("keeps the same ids and the sent state across an unmount and remount", async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(ok());
    vi.stubGlobal("fetch", fetchMock);
    render(<Harness />);

    await userEvent.click(screen.getByRole("button", { name: "Send a test trace" }));
    await userEvent.click(screen.getByRole("button", { name: "Toggle" }));
    await userEvent.click(screen.getByRole("button", { name: "Toggle" }));
    await userEvent.click(screen.getByRole("button", { name: "Send a test trace" }));
    expect(sentIds(fetchMock, 1)).toEqual(sentIds(fetchMock, 0));

    await userEvent.click(screen.getByRole("button", { name: "Toggle" }));
    await userEvent.click(screen.getByRole("button", { name: "Toggle" }));
    expect(screen.getByRole("button", { name: "Send a test trace" })).toBeDisabled();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("does not offer a retry when the key is rejected", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 401 })));
    render(<Harness />);

    await userEvent.click(screen.getByRole("button", { name: "Send a test trace" }));

    expect(screen.getByRole("alert")).toHaveTextContent(
      "The key was rejected. Create a new key and try again.",
    );
    expect(screen.getByRole("button", { name: "Send a test trace" })).toBeDisabled();
  });

  it("renders nothing without a key", () => {
    render(<Harness apiKey={null} />);
    expect(screen.queryByRole("button", { name: "Send a test trace" })).not.toBeInTheDocument();
  });
});

describe("WaitingForTrace", () => {
  it("announces the sent test trace in a status region and drops the helper", () => {
    render(<WaitingForTrace error={null} onRetry={vi.fn()} testSent />);
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("Test trace sent. Waiting for it to arrive…");
    expect(status).not.toHaveTextContent("Send a test trace or run a snippet.");
  });
});
