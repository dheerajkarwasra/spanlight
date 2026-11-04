/// <reference types="node" />
import { createServer, type ServerResponse } from "node:http";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterAll, beforeAll, expect, it, vi } from "vitest";
import {
  useTraceListQuery,
  useTraceQuery,
  useTraceHistogramQuery,
  useTraceCountQuery,
  useSpanQuery,
  useStepSpanQuery,
  usePreviousRunsQuery,
  useTraceComparisonQuery,
} from "./trace-queries";
import { useOverviewQueries, useTracesTabQuery } from "@/features/overview/use-overview-queries";
import { useSessionListQuery, useSessionTurnsQuery } from "@/features/sessions/session-queries";
import type { Span } from "@/lib/api";

const scope = vi.hoisted(() => ({ projectId: "old-project" }));
vi.mock("@/features/shell/project-context", () => ({
  useProjectParams: () => scope,
  useProjectFilters: () => ({
    range: {
      value: "24h",
      durationMs: 86400000,
      from: "2026-10-01T00:00:00Z",
      to: "2026-10-02T00:00:00Z",
    },
    environment: "prod",
  }),
}));
let origin: string;
const pending = new Map<string, ServerResponse>();
const closed = new Set<string>();
const server = createServer((request, response) => {
  const url = new URL(request.url ?? "/", "http://local");
  const label = request.url ?? "/";
  if (
    url.searchParams.get("q") === "current" ||
    ((url.searchParams.get("q") === "paged" || url.pathname.includes("paged-session")) &&
      !url.searchParams.has("cursor"))
  ) {
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(
      JSON.stringify({
        items: [{ trace_id: "current-project-trace", name: url.pathname.split("/")[4] }],
        next_cursor:
          url.searchParams.get("q") === "paged" || url.pathname.includes("paged-session")
            ? "next"
            : null,
      }),
    );
  } else {
    pending.set(label, response);
    response.on("close", () => closed.add(label));
  }
});
beforeAll(async () => {
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("No HTTP port");
  origin = `http://127.0.0.1:${address.port}`;
});
afterAll(
  () =>
    new Promise<void>((resolve) => {
      server.closeAllConnections();
      server.close(() => resolve());
    }),
);
it("aborts navigation, rapid filter replacements and project switch; current data wins", async () => {
  const nativeFetch = globalThis.fetch;
  vi.spyOn(globalThis, "fetch").mockImplementation((path, options) =>
    nativeFetch(
      new URL(typeof path === "string" ? path : path instanceof URL ? path.href : path.url, origin),
      options,
    ),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  const first = renderHook(() => useTraceListQuery({ q: "navigation" }), { wrapper });
  await waitFor(() => expect(pending.size).toBe(1));
  first.unmount();
  await waitFor(() => expect(closed.size).toBe(1), { timeout: 1000 });
  const hook = renderHook(({ q }) => useTraceListQuery({ q }), {
    wrapper,
    initialProps: { q: "first" },
  });
  await waitFor(() => expect(pending.size).toBe(2));
  hook.rerender({ q: "second" });
  await waitFor(() => expect(pending.size).toBe(3));
  await waitFor(() => expect(closed.size).toBe(2));
  scope.projectId = "current-project";
  hook.rerender({ q: "current" });
  await waitFor(() => expect(closed.size).toBe(3));
  await waitFor(() =>
    expect(hook.result.current.data?.pages[0]?.items[0]?.trace_id).toBe("current-project-trace"),
  );
  act(() => {
    for (const response of pending.values())
      response.end(JSON.stringify({ items: [{ trace_id: "stale" }], next_cursor: null }));
  });
  expect(hook.result.current.data?.pages[0]?.items[0]?.trace_id).toBe("current-project-trace");
  hook.unmount();
  client.clear();
});

const surfaces = [
  ["overview and its dependencies", () => useOverviewQueries(), 12],
  ["trace detail", () => useTraceQuery("t"), 1],
  ["histogram", () => useTraceHistogramQuery({}), 1],
  ["trace count", () => useTraceCountQuery(), 1],
  ["span payload", () => useSpanQuery("t", { span_id: "s" } as Span, true), 1],
  ["compare span", () => useStepSpanQuery("t", "s"), 1],
  ["previous runs", () => usePreviousRunsQuery("trace", "2026-10-02T00:00:00Z"), 1],
  ["comparison", () => useTraceComparisonQuery("a", "b"), 1],
  ["overview live traces", () => useTracesTabQuery("latest"), 1],
  ["sessions", () => useSessionListQuery({ sort: "last", order: "desc" }, false), 1],
  ["session turns", () => useSessionTurnsQuery("session/a"), 1],
] as const;
it.each(surfaces)("aborts abandoned %s requests", async (_name, read, count) => {
  pending.clear();
  closed.clear();
  const nativeFetch = globalThis.fetch;
  vi.spyOn(globalThis, "fetch").mockImplementation((path, options) =>
    nativeFetch(
      new URL(typeof path === "string" ? path : path instanceof URL ? path.href : path.url, origin),
      options,
    ),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  const hook = renderHook(() => read(), { wrapper });
  await waitFor(() => expect(pending.size).toBe(count), { timeout: 1500 });
  hook.unmount();
  await waitFor(() => expect(closed.size).toBe(count), { timeout: 1500 });
  client.clear();
});

// Exhaust every three-step sequence of filter replacement and project replacement.
it.each(Array.from({ length: 8 }, (_, bits) => bits))(
  "keeps only the current result after transition sequence %i",
  async (bits) => {
    pending.clear();
    closed.clear();
    scope.projectId = "sequence-start";
    const nativeFetch = globalThis.fetch;
    vi.spyOn(globalThis, "fetch").mockImplementation((path, options) =>
      nativeFetch(
        new URL(
          typeof path === "string" ? path : path instanceof URL ? path.href : path.url,
          origin,
        ),
        options,
      ),
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const hook = renderHook(({ q }) => useTraceListQuery({ q }), {
      wrapper,
      initialProps: { q: "sequence-0" },
    });
    await waitFor(() => expect(pending.size).toBe(1));
    let q = "sequence-0";
    for (let step = 0; step < 3; step++) {
      if (bits & (1 << step)) scope.projectId = `project-${step}`;
      else q = `sequence-${step + 1}`;
      hook.rerender({ q });
      await waitFor(() => expect(pending.size).toBe(step + 2));
      await waitFor(() => expect(closed.size).toBe(step + 1));
    }
    hook.rerender({ q: "current" });
    await waitFor(() => expect(closed.size).toBe(4));
    await waitFor(() =>
      expect(hook.result.current.data?.pages[0]?.items[0]?.trace_id).toBe("current-project-trace"),
    );
    expect(hook.result.current.data?.pages[0]?.items[0]?.name).toBe(scope.projectId);
    hook.unmount();
    client.clear();
  },
);

it.each([
  ["traces", () => useTraceListQuery({ q: "paged" })],
  ["sessions", () => useSessionListQuery({ sort: "last", order: "desc", q: "paged" }, false)],
  ["session turns", () => useSessionTurnsQuery("paged-session")],
] as const)("aborts an abandoned next page of %s", async (_name, read) => {
  pending.clear();
  closed.clear();
  const nativeFetch = globalThis.fetch;
  vi.spyOn(globalThis, "fetch").mockImplementation((path, options) =>
    nativeFetch(
      new URL(typeof path === "string" ? path : path instanceof URL ? path.href : path.url, origin),
      options,
    ),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  const hook = renderHook(() => read(), { wrapper });
  await waitFor(() => expect(hook.result.current.hasNextPage).toBe(true));
  act(() => {
    void hook.result.current.fetchNextPage();
  });
  await waitFor(() => expect(pending.size).toBe(1));
  hook.unmount();
  await waitFor(() => expect(closed.size).toBe(1));
  client.clear();
});
