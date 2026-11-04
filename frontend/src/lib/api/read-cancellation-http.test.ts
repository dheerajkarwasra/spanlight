/// <reference types="node" />
import { createServer } from "node:http";
import { afterAll, beforeAll, expect, it, vi } from "vitest";
import { projectsApi } from "./projects";
import { insightsApi } from "./insights";
import { budgetsApi } from "./budgets";
import { alertsApi } from "./alerts";
import { releasesApi } from "./releases";
import { providersApi } from "./providers";
import { completenessApi } from "./completeness";

const windowQuery = { from: "2026-10-01T00:00:00Z", to: "2026-10-02T00:00:00Z" };
const reads = [
  ["overview", projectsApi.overview, ["p", windowQuery]],
  ["timeseries", projectsApi.timeseries, ["p", { ...windowQuery, bucket: "hour" }]],
  ["models", projectsApi.models, ["p", windowQuery]],
  ["traces", projectsApi.traces, ["p", windowQuery]],
  ["histogram", projectsApi.traceHistogram, ["p", windowQuery]],
  ["trace", projectsApi.trace, ["p", "t"]],
  ["span", projectsApi.span, ["p", "t", "s"]],
  ["sessions", projectsApi.sessions, ["p", windowQuery]],
  ["turns", projectsApi.sessionTurns, ["p", "session/a", {}]],
  ["health", insightsApi.health, ["p", {}]],
  ["budgets", budgetsApi.list, ["p"]],
  ["compare", projectsApi.compareTraces, ["p", "a", "b"]],
  ["filters", projectsApi.filters, ["p"]],
  ["onboarding", projectsApi.onboarding, ["p"]],
  ["findings", insightsApi.list, ["p", {}]],
  ["detector runs", insightsApi.detectorRuns, ["p", 20]],
  ["alert rules", alertsApi.rules, ["p"]],
  ["releases", releasesApi.list, ["p", {}]],
  ["incidents", providersApi.incidents, ["p", {}]],
  ["completeness", completenessApi.get, ["p", {}]],
] as const;
let origin: string;
let requestStarted: (() => void) | undefined;
const server = createServer((_request, response) => {
  requestStarted?.();
  requestStarted = undefined;
  const timer = setTimeout(() => {
    response.writeHead(200, {
      "Content-Type": "application/json",
      "Aggregate-Status": "unavailable",
    });
    response.end("[]");
  }, 150);
  response.on("close", () => clearTimeout(timer));
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
it.each(reads)("%s rejects an abandoned real HTTP read", async (_name, read, args) => {
  const realFetch = globalThis.fetch;
  vi.spyOn(globalThis, "fetch").mockImplementation((path, options) =>
    realFetch(
      new URL(typeof path === "string" ? path : path instanceof URL ? path.href : path.url, origin),
      options,
    ),
  );
  const controller = new AbortController();
  const started = new Promise<void>((resolve) => {
    requestStarted = resolve;
  });
  const result: unknown = Reflect.apply(read, undefined, [...args, controller.signal]);
  const rejection = expect(result).rejects.toMatchObject({ name: "AbortError" });
  await started;
  controller.abort(new DOMException("Abandoned read", "AbortError"));
  await rejection;
});

it.each(reads)("%s completes normally without a signal", async (name, read, args) => {
  const realFetch = globalThis.fetch;
  vi.spyOn(globalThis, "fetch").mockImplementation((path, options) =>
    realFetch(
      new URL(typeof path === "string" ? path : path instanceof URL ? path.href : path.url, origin),
      options,
    ),
  );
  const result: unknown = await Reflect.apply(read, undefined, args);
  expect(result).toEqual(
    name === "models" || name === "timeseries"
      ? { items: [], aggregate_status: "unavailable", oldest_pending_at: null }
      : [],
  );
});

it.each(reads)("%s completes with an active signal", async (name, read, args) => {
  const realFetch = globalThis.fetch;
  vi.spyOn(globalThis, "fetch").mockImplementation((path, options) =>
    realFetch(
      new URL(typeof path === "string" ? path : path instanceof URL ? path.href : path.url, origin),
      options,
    ),
  );
  const controller = new AbortController();
  const result: unknown = await Reflect.apply(read, undefined, [...args, controller.signal]);
  controller.abort(new DOMException("Completed", "AbortError"));
  expect(result).toEqual(
    name === "models" || name === "timeseries"
      ? { items: [], aggregate_status: "unavailable", oldest_pending_at: null }
      : [],
  );
});
