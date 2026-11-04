import { describe, expect, it } from "vitest";

import { createQueryClient } from "@/app/query-client";
import { createAppRouter } from "@/app/router";
import { LEGAL_PATHS } from "@/features/landing/links";

import { RESERVED_FIRST_SEGMENTS } from "./slug-map";

/** Paths the web server routes to the backend or to static files, not to the router. */
const SERVER_PATHS = ["api", "v1", "gw", "health", "docs", "assets"];

describe("RESERVED_FIRST_SEGMENTS", () => {
  it("covers every static top-level path of the router", () => {
    const router = createAppRouter(createQueryClient());
    const firstSegments = new Set(
      Object.keys(router.routesByPath)
        .map((path) => path.split("/")[1] ?? "")
        .filter((segment) => segment !== "" && !segment.startsWith("$")),
    );

    expect(firstSegments.size).toBeGreaterThan(0);
    for (const segment of firstSegments) {
      expect(RESERVED_FIRST_SEGMENTS.has(segment), segment).toBe(true);
    }
  });

  // The legal routes exist only in a VITE_LEGAL_PAGES build, so the router check above misses them.
  it("covers every legal page path", () => {
    for (const path of Object.values(LEGAL_PATHS)) {
      expect(RESERVED_FIRST_SEGMENTS.has(path.slice(1)), path).toBe(true);
    }
  });

  it("covers the paths the web server owns", () => {
    for (const segment of SERVER_PATHS) {
      expect(RESERVED_FIRST_SEGMENTS.has(segment), segment).toBe(true);
    }
  });
});
