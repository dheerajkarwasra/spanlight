import { createServer } from "node:http";
import { afterAll, beforeAll, expect, it } from "vitest";
import { QueryClient } from "@tanstack/react-query";
import { api } from "./client";

const server = createServer((request, response) => {
  response.setHeader("Content-Type", "application/json");
  const status = request.url?.slice(1) || "unavailable";
  if (status !== "missing") response.setHeader("Aggregate-Status", status);
  if (status === "pending")
    response.setHeader("Aggregate-Oldest-Pending-At", "2026-10-01T00:00:00Z");
  response.end("[]");
});
let url: string;
beforeAll(async () => {
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("No HTTP port");
  url = `http://127.0.0.1:${address.port}`;
});
afterAll(() => new Promise<void>((resolve) => server.close(() => resolve())));
it("keeps unavailable coverage on an empty HTTP list through query caching", async () => {
  const cache = new QueryClient();
  await cache.query({ queryKey: ["aggregate"], queryFn: () => api.getAggregateList(url) });
  const result = await cache.query({
    queryKey: ["aggregate"],
    queryFn: () => api.getAggregateList(url),
  });
  expect(result).toEqual({ items: [], aggregate_status: "unavailable", oldest_pending_at: null });
});

it.each(["complete", "pending", "unavailable"] as const)(
  "keeps %s list headers without changing its array body",
  async (status) => {
    const result = await api.getAggregateList(`${url}/${status}`);
    expect(result.items).toEqual([]);
    expect(result.aggregate_status).toBe(status);
    expect(result.oldest_pending_at).toBe(status === "pending" ? "2026-10-01T00:00:00Z" : null);
  },
);
it("does not invent complete coverage for an old server without headers", async () => {
  expect(await api.getAggregateList(`${url}/missing`)).toEqual({
    items: [],
    oldest_pending_at: null,
  });
});
