/// <reference types="node" />
import { createServer, type Server, type ServerResponse } from "node:http";

import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  authApi,
  projectsApi,
  queryKeys,
  type OverviewMetrics,
  type Page,
  type TraceSummary,
} from "@/lib/api";
import { recallOpenedTrace, rememberOpenedTrace } from "@/features/traces/last-traces-search";
import { makeProject, mockSignedIn, ME, ORG_ID, renderApp } from "@/test/render-app";

const trace: TraceSummary = {
  trace_id: "shared-id",
  name: "Private project A trace",
  started_at: "2026-10-17T08:00:00Z",
  tags: [],
  models: [],
  scores: [],
  metadata: {},
} as unknown as TraceSummary;
const metrics: OverviewMetrics = {
  current: {
    traces: 12345,
    llm_calls: 432,
    error_rate: 0,
    p50_ms: 400,
    p95_ms: 1200,
    cost_usd: "1.50",
    unpriced_calls: 0,
    input_tokens: 100,
    output_tokens: 50,
  },
  previous: {
    traces: 0,
    llm_calls: 0,
    error_rate: 0,
    p50_ms: null,
    p95_ms: null,
    cost_usd: "0",
    unpriced_calls: 0,
    input_tokens: 0,
    output_tokens: 0,
  },
  approximate: false,
};

let server: Server;
let origin: string;
let waiting: ServerResponse[];
let holdA: boolean;
let startedB: boolean;

function respond(response: ServerResponse, body: unknown) {
  response.writeHead(200, { "Content-Type": "application/json" });
  response.end(JSON.stringify(body));
}

beforeEach(async () => {
  waiting = [];
  holdA = false;
  startedB = false;
  server = createServer((request, response) => {
    if (request.url?.startsWith("/proj_2/") || holdA) {
      startedB ||= request.url?.startsWith("/proj_2/") ?? false;
      waiting.push(response);
      return;
    }
    respond(
      response,
      request.url?.endsWith("/overview") ? metrics : { items: [trace], next_cursor: null },
    );
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("Expected TCP server address");
  origin = `http://127.0.0.1:${address.port}`;
  mockSignedIn([makeProject("proj_1", "Checkout"), makeProject("proj_2", "Search")]);
  vi.spyOn(projectsApi, "traces").mockImplementation(async (projectId) => {
    const response = await fetch(`${origin}/${projectId}/traces`);
    return response.json() as Promise<Page<TraceSummary>>;
  });
  vi.spyOn(projectsApi, "overview").mockImplementation(async (projectId) => {
    const response = await fetch(`${origin}/${projectId}/overview`);
    if (!response.ok) throw new ApiError(response.status, {});
    return response.json() as Promise<OverviewMetrics>;
  });
  // These tests isolate display scope; unrelated optional reads may fail independently.
  vi.spyOn(projectsApi, "traceHistogram").mockRejectedValue(new ApiError(500, {}));
});

afterEach(async () => {
  server.closeAllConnections();
  await new Promise<void>((resolve) => server.close(() => resolve()));
});

describe("project display scope with real delayed HTTP", () => {
  it("hides A overview metrics while B loads, then safely returns to cached A", async () => {
    const router = renderApp(`/${ORG_ID}/proj_1/overview`);
    await screen.findAllByText("432");
    await act(() =>
      router.navigate({
        to: "/$orgId/$projectId/overview",
        params: { orgId: ORG_ID, projectId: "proj_2" },
      }),
    );
    await waitFor(() => expect(startedB).toBe(true));
    expect(
      screen.getAllByRole("button", { name: "Project Search. Switch project or organization" })
        .length,
    ).toBeGreaterThan(0);
    expect(screen.queryAllByText("432")).toHaveLength(0);
    expect(screen.getByText("Loading overview")).toBeInTheDocument();
    await act(() =>
      router.navigate({
        to: "/$orgId/$projectId/overview",
        params: { orgId: ORG_ID, projectId: "proj_1" },
      }),
    );
    await screen.findAllByText("432");
    act(() => {
      for (const response of waiting)
        respond(response, {
          ...metrics,
          current: { ...metrics.current, traces: 99999, llm_calls: 99999 },
        });
    });
    expect(screen.queryAllByText("99,999")).toHaveLength(0);
    expect(screen.getAllByText("432").length).toBeGreaterThan(0);
  });

  it("keeps overview metrics during a time-range change inside the same project", async () => {
    const router = renderApp(`/${ORG_ID}/proj_1/overview`);
    await screen.findAllByText("432");
    holdA = true;
    await act(() =>
      router.navigate({
        to: "/$orgId/$projectId/overview",
        params: { orgId: ORG_ID, projectId: "proj_1" },
        search: { range: "7d" },
      }),
    );
    await waitFor(() => expect(waiting.length).toBeGreaterThan(0));
    expect(screen.getAllByText("432").length).toBeGreaterThan(0);
    expect(screen.queryByText("Loading overview")).not.toBeInTheDocument();
  });

  it("shows a denied project read without resurfacing the previous project's metrics", async () => {
    const router = renderApp(`/${ORG_ID}/proj_1/overview`);
    await screen.findAllByText("432");
    await act(() =>
      router.navigate({
        to: "/$orgId/$projectId/overview",
        params: { orgId: ORG_ID, projectId: "proj_2" },
      }),
    );
    await waitFor(() => expect(startedB).toBe(true));
    act(() => {
      for (const response of waiting) {
        response.writeHead(403, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ detail: "Access revoked" }));
      }
    });
    await screen.findByText("Couldn't load the overview");
    expect(screen.queryAllByText("432")).toHaveLength(0);
  });

  it("drops trace rows, counts and bulk selections across projects, even with the same trace ID", async () => {
    const router = renderApp(`/${ORG_ID}/proj_1/traces`);
    await userEvent.click(
      await screen.findByRole("checkbox", { name: "Select Private project A trace shared-id" }),
    );
    expect(screen.getByRole("region", { name: "Selected traces" })).toBeInTheDocument();
    await act(() =>
      router.navigate({
        to: "/$orgId/$projectId/traces",
        params: { orgId: ORG_ID, projectId: "proj_2" },
      }),
    );
    await waitFor(() => expect(startedB).toBe(true));
    expect(
      screen.getAllByRole("button", { name: "Project Search. Switch project or organization" })
        .length,
    ).toBeGreaterThan(0);
    expect(screen.queryByRole("region", { name: "Selected traces" })).not.toBeInTheDocument();
    expect(screen.queryAllByText("Private project A trace")).toHaveLength(0);
    expect(screen.queryByText(/12,345 traces/)).not.toBeInTheDocument();
    await act(() =>
      router.navigate({
        to: "/$orgId/$projectId/traces",
        params: { orgId: ORG_ID, projectId: "proj_1" },
      }),
    );
    await screen.findByRole("checkbox", { name: "Select Private project A trace shared-id" });
    expect(screen.queryByRole("region", { name: "Selected traces" })).not.toBeInTheDocument();
  });

  it("resets local selection when the signed-in identity changes on the same project", async () => {
    const router = renderApp(`/${ORG_ID}/proj_1/traces`);
    await userEvent.click(
      await screen.findByRole("checkbox", { name: "Select Private project A trace shared-id" }),
    );
    expect(screen.getByRole("region", { name: "Selected traces" })).toBeInTheDocument();
    act(() => {
      router.options.context.queryClient.setQueryData(queryKeys.me, {
        ...ME,
        user: { ...ME.user, id: "second-user" },
      });
    });
    await waitFor(() =>
      expect(screen.queryByRole("region", { name: "Selected traces" })).not.toBeInTheDocument(),
    );
  });

  it("clears cached project data and remembered selection on sign out", async () => {
    vi.spyOn(authApi, "logout").mockResolvedValue(undefined);
    const router = renderApp(`/${ORG_ID}/proj_1/traces`);
    await screen.findByRole("checkbox", { name: "Select Private project A trace shared-id" });
    const scope = JSON.stringify([ME.user.id, ORG_ID, "proj_1"]);
    rememberOpenedTrace("shared-id", scope);
    vi.mocked(authApi.me).mockRejectedValue(new ApiError(401, {}));
    const accountMenu = screen.getAllByRole("button", { name: /Account menu/ })[0];
    if (!accountMenu) throw new Error("Expected account menu");
    await userEvent.click(accountMenu);
    await userEvent.click(await screen.findByRole("menuitem", { name: "Sign out" }));
    await screen.findByRole("button", { name: "Sign in" });
    expect(
      router.options.context.queryClient.getQueryCache().findAll({
        queryKey: queryKeys.project("proj_1").all,
        predicate: (query) => query.state.data !== undefined,
      }),
    ).toHaveLength(0);
    expect(recallOpenedTrace(scope)).toBeNull();
  });

  it("retains rows and selection while filters change within the project", async () => {
    const router = renderApp(`/${ORG_ID}/proj_1/traces`);
    await userEvent.click(
      await screen.findByRole("checkbox", { name: "Select Private project A trace shared-id" }),
    );
    holdA = true;
    await act(() =>
      router.navigate({
        to: "/$orgId/$projectId/traces",
        params: { orgId: ORG_ID, projectId: "proj_1" },
        search: { q: "pending filter" },
      }),
    );
    await waitFor(() => expect(waiting.length).toBeGreaterThan(0));
    expect(screen.getAllByText("Private project A trace").length).toBeGreaterThan(0);
    expect(screen.getByRole("region", { name: "Selected traces" })).toBeInTheDocument();
  });
});
