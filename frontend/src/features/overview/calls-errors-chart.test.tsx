import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Band } from "@/lib/incident-bands";

import { CallsErrorsChart } from "./calls-errors-chart";
import type { ChartPoint } from "./chart-data";
import { INCIDENT_OVERLAY_KEY } from "./use-incident-overlay";

const POINTS: ChartPoint[] = ["00", "01", "02", "03"].map((hour, index) => ({
  bucketStart: `2026-10-16T${hour}:00:00Z`,
  llmCalls: 100 + index,
  errors: index,
  succeeded: 100,
  partial: false,
  p95Ms: null,
  costUsd: null,
  tokens: 0,
}));

const BANDS: Band[] = [
  {
    start: new Date("2026-10-16T01:00:00Z"),
    end: new Date("2026-10-16T02:00:00Z"),
    title: "OpenAI · Elevated error rates across the API (critical)",
    critical: true,
  },
  {
    start: new Date("2026-10-16T02:00:00Z"),
    end: new Date("2026-10-16T03:00:00Z"),
    title: "Anthropic · Elevated errors on Claude Sonnet 4.5 (major)",
    critical: false,
  },
];

const DATA = {
  points: POINTS,
  bucket: "hour" as const,
  rangeLabel: "last 24h",
  now: new Date("2026-10-16T04:00:00Z"),
};

/** Recharts measures its container; jsdom lays nothing out, so give every element a size. */
function giveElementsASize() {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    width: 600,
    height: 200,
    top: 0,
    left: 0,
    right: 600,
    bottom: 200,
    x: 0,
    y: 0,
    toJSON: () => ({}),
  });
  for (const [name, value] of [
    ["clientWidth", 600],
    ["clientHeight", 200],
    ["offsetWidth", 600],
    ["offsetHeight", 200],
  ] as const) {
    Object.defineProperty(HTMLElement.prototype, name, { configurable: true, get: () => value });
  }
}

function prefersReducedMotion(reduce: boolean) {
  vi.spyOn(window, "matchMedia").mockImplementation(
    (query: string) =>
      ({
        matches: reduce && query.includes("prefers-reduced-motion"),
        media: query,
        onchange: null,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
        addListener: () => undefined,
        removeListener: () => undefined,
        dispatchEvent: () => false,
      }) as MediaQueryList,
  );
}

function bandRects(container: HTMLElement) {
  return container.querySelectorAll(".recharts-reference-area");
}

describe("CallsErrorsChart incident overlay", () => {
  beforeEach(() => {
    window.localStorage.clear();
    giveElementsASize();
  });

  it("draws one band per incident, hatching the critical one, with legend and summary", () => {
    const { container } = render(
      <CallsErrorsChart data={DATA} bands={BANDS} isRefreshing={false} />,
    );

    expect(bandRects(container)).toHaveLength(2);
    expect(container.querySelectorAll(".incident-band-hatch")).toHaveLength(1);
    expect(screen.getByText("Provider incident")).toBeInTheDocument();
    expect(screen.getByText("Critical (hatched)")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Show incidents" })).toBeChecked();
    expect(screen.getByRole("img")).toHaveAccessibleName(
      expect.stringContaining("2 provider incidents: OpenAI · Elevated error rates"),
    );
  });

  it("hides the bands when switched off and remembers it", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <CallsErrorsChart data={DATA} bands={BANDS} isRefreshing={false} />,
    );

    await user.click(screen.getByRole("switch", { name: "Show incidents" }));

    expect(bandRects(container)).toHaveLength(0);
    expect(screen.queryByText("Provider incident")).not.toBeInTheDocument();
    expect(window.localStorage.getItem(INCIDENT_OVERLAY_KEY)).toBe("off");
  });

  it("opens with the bands off when the viewer turned them off before", () => {
    window.localStorage.setItem(INCIDENT_OVERLAY_KEY, "off");
    const { container } = render(
      <CallsErrorsChart data={DATA} bands={BANDS} isRefreshing={false} />,
    );

    expect(screen.getByRole("switch", { name: "Show incidents" })).not.toBeChecked();
    expect(bandRects(container)).toHaveLength(0);
  });

  it("falls back to on, and still toggles, when storage throws", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    const user = userEvent.setup();
    const { container } = render(
      <CallsErrorsChart data={DATA} bands={BANDS} isRefreshing={false} />,
    );

    expect(bandRects(container)).toHaveLength(2);
    await user.click(screen.getByRole("switch", { name: "Show incidents" }));
    expect(bandRects(container)).toHaveLength(0);
  });

  it("fades the bands in, but not under reduced motion", () => {
    prefersReducedMotion(false);
    const first = render(<CallsErrorsChart data={DATA} bands={BANDS} isRefreshing={false} />);
    expect(bandRects(first.container)[0]).toHaveClass("animate-fade-in");
    first.unmount();

    prefersReducedMotion(true);
    const second = render(<CallsErrorsChart data={DATA} bands={BANDS} isRefreshing={false} />);
    expect(bandRects(second.container)).toHaveLength(2);
    expect(bandRects(second.container)[0]).not.toHaveClass("animate-fade-in");
  });

  it("lists the overlapping incidents per hour in the table view", async () => {
    const user = userEvent.setup();
    render(<CallsErrorsChart data={DATA} bands={BANDS} isRefreshing={false} />);

    await user.click(screen.getByRole("button", { name: "Show table" }));

    const table = screen.getByRole("table");
    expect(screen.getByRole("region", { name: /, table$/ })).toHaveAttribute("tabindex", "0");
    expect(within(table).getByRole("columnheader", { name: "Provider incidents" })).toBeVisible();
    const rows = within(table).getAllByRole("row");
    expect(rows[1]).toHaveTextContent("none");
    expect(rows[2]).toHaveTextContent("OpenAI · Elevated error rates across the API (critical)");
    expect(rows[3]).toHaveTextContent("Anthropic · Elevated errors on Claude Sonnet 4.5 (major)");
    expect(screen.getByRole("button", { name: "Show chart" })).not.toHaveAttribute("aria-pressed");
  });

  it("replaces the switch with the server note when status polling is off", () => {
    const { container } = render(
      <CallsErrorsChart data={DATA} bands={[]} pollingOff isRefreshing={false} />,
    );

    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
    expect(screen.getByText(/Provider status feeds are off on this server/)).toBeInTheDocument();
    expect(bandRects(container)).toHaveLength(0);
  });

  it("offers no switch while incidents are unknown", () => {
    render(<CallsErrorsChart data={DATA} isRefreshing={false} />);

    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
  });
});
