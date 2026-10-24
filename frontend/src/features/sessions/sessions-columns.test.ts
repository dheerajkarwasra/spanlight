import { describe, expect, it } from "vitest";

import { visibleColumns } from "@/components/data-table";

import { sessionColumns } from "./sessions-columns";

/** The Table settings track the table adds after the columns. */
const SETTINGS_PX = 40;

/** The smallest width a track can take: "96px" → 96, "minmax(220px,1.2fr)" → 220. */
function minPx(width: string): number {
  const match = /(\d+)px/.exec(width);
  if (!match) throw new Error(`no px minimum in ${width}`);
  return Number(match[1]);
}

const columns = sessionColumns({
  orgId: "org_1",
  projectId: "proj_1",
  costP99: null,
  previewsPending: false,
});

describe("sessionColumns", () => {
  it.each([640, 700, 800, 900, 1024, 1300])(
    "fits its default columns in a %i px table without scrolling sideways",
    (width) => {
      const shown = visibleColumns(columns, {}, width);
      const needed = shown.reduce((sum, col) => sum + minPx(col.width), SETTINGS_PX);
      expect(needed).toBeLessThanOrEqual(width);
    },
  );

  it("keeps the session, turns, tokens, cost and errors at any width", () => {
    const ids = visibleColumns(columns, {}, 640).map((col) => col.id);
    expect(ids).toEqual(["session", "turns", "tokens", "cost", "errors"]);
  });
});
