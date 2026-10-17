import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import { ApiError, billingSourcesApi, type BillingSource } from "@/lib/api";

import { SourceRow } from "./source-row";

const toasts = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock("sonner", () => ({ toast: toasts }));

// The router is not mounted here: a link renders the reconciliation path it was given.
vi.mock("@tanstack/react-router", () => ({
  Link: ({
    children,
    className,
    params,
  }: {
    children: ReactNode;
    className?: string;
    params: { orgId: string; projectId: string; sourceId: string };
  }) => (
    <a
      href={`/${params.orgId}/${params.projectId}/settings/billing-sources/${params.sourceId}`}
      className={className}
    >
      {children}
    </a>
  ),
}));

vi.mock("@/features/shell/project-context", () => ({
  useProjectParams: () => ({ orgId: "org_1", projectId: "proj_1" }),
}));

const NOW = new Date("2026-10-16T12:00:00Z");

function source(overrides: Partial<BillingSource> = {}): BillingSource {
  return {
    id: "src_1",
    kind: "anthropic_admin_api",
    provider: "anthropic",
    name: "Prod account",
    provider_scope: null,
    enabled: true,
    status: "ok",
    key_set: true,
    key_id: "v1",
    last_fetched_at: "2026-10-16T10:00:00Z",
    last_success_at: "2026-10-16T10:00:00Z",
    last_error: null,
    projects: [{ id: "p1", name: "Support Copilot", slug: "support-copilot" }],
    created_by: null,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    ...overrides,
  };
}

function renderRow(row: BillingSource, canManage = true) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <TooltipProvider delayDuration={0}>
        <ul>
          <SourceRow source={row} canManage={canManage} />
        </ul>
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});

afterEach(() => {
  vi.useRealTimers();
});

describe("SourceRow name", () => {
  it("links to the source's reconciliation page", () => {
    renderRow(source());
    expect(screen.getByRole("link", { name: "Prod account" })).toHaveAttribute(
      "href",
      "/org_1/proj_1/settings/billing-sources/src_1",
    );
  });
});

describe("SourceRow status chip", () => {
  it.each([
    [
      source(),
      "OK · fetched 2 h ago",
      "Status: OK, fetched 2 h ago",
      "Last fetched Oct 16, 10:00 UTC",
    ],
    [
      source({ status: "failing", last_error: "401 Unauthorized" }),
      "Failing · 401 Unauthorized",
      "Status: Failing, 401 Unauthorized",
      "The last fetch failed: 401 Unauthorized",
    ],
    [
      source({ status: "never_fetched", last_fetched_at: null, last_success_at: null }),
      "Never fetched",
      "Status: Never fetched",
      "The first fetch runs at 03:30 UTC, or now with Refresh.",
    ],
  ])("shows the status and explains it on focus", async (row, text, name, tooltip) => {
    renderRow(row);

    const chip = screen.getByRole("img", { name });
    expect(chip).toHaveTextContent(text);
    act(() => {
      chip.focus();
    });
    expect(await screen.findByRole("tooltip")).toHaveTextContent(tooltip);
  });
});

describe("SourceRow actions", () => {
  it("promises no live update after a refresh, since nothing polls the run", async () => {
    const user = userEvent.setup();
    vi.spyOn(billingSourcesApi, "refresh").mockResolvedValue({ run_id: "run_1" });
    renderRow(source());

    await user.click(screen.getByRole("button", { name: "Refresh Prod account" }));
    // The mutation settles on resolved promises; the frozen clock rules out a timed wait.
    await act(async () => {
      await Promise.resolve();
    });

    expect(toasts.success).toHaveBeenCalledWith(
      "Refresh queued. Reload in a minute to see the new figures.",
    );
  });

  it("disables Refresh with a countdown after the server rate-limits it", async () => {
    const user = userEvent.setup();
    const refresh = vi
      .spyOn(billingSourcesApi, "refresh")
      .mockRejectedValue(new ApiError(429, { code: "RATE_LIMITED" }, 252));
    renderRow(source());

    await user.click(screen.getByRole("button", { name: "Refresh Prod account" }));

    expect(refresh).toHaveBeenCalledWith("org_1", "src_1");
    const button = await screen.findByRole("button", { name: /Refresh Prod account/ });
    expect(button).toHaveTextContent("Refresh · 4:12");
    expect(button).toHaveAttribute("aria-disabled", "true");
    await user.click(button);
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("offers Refresh again once the wait is over", async () => {
    const user = userEvent.setup();
    vi.spyOn(billingSourcesApi, "refresh").mockRejectedValue(
      new ApiError(429, { code: "RATE_LIMITED" }, 3),
    );
    renderRow(source());

    await user.click(screen.getByRole("button", { name: "Refresh Prod account" }));
    const button = await screen.findByRole("button", { name: /Refresh Prod account/ });
    expect(button).toHaveTextContent("Refresh · 0:03");

    vi.setSystemTime(NOW.getTime() + 4_000);
    await waitFor(
      () => {
        expect(button).toHaveTextContent(/^Refresh$/);
      },
      { timeout: 2_500 },
    );
    expect(button).not.toHaveAttribute("aria-disabled");
  });

  it("offers Upload instead of Refresh for a CSV source only", () => {
    renderRow(source({ kind: "csv_upload", provider: "google", key_set: false }));

    expect(screen.getByRole("button", { name: "Upload a CSV to Prod account" })).toBeVisible();
    expect(screen.queryByRole("button", { name: /^Refresh/ })).not.toBeInTheDocument();
  });

  it("offers Refresh, not Upload, for an API source", () => {
    renderRow(source());

    expect(screen.getByRole("button", { name: "Refresh Prod account" })).toBeVisible();
    expect(screen.queryByRole("button", { name: /^Upload/ })).not.toBeInTheDocument();
  });

  it("shows Edit and Delete to managers", () => {
    renderRow(source());

    const item = screen.getByRole("listitem");
    expect(within(item).getByRole("button", { name: "Edit Prod account" })).toBeVisible();
    expect(within(item).getByRole("button", { name: "Delete Prod account" })).toBeVisible();
  });

  it("hides every action from a role that can only read", () => {
    renderRow(source(), false);

    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.getByText("Prod account")).toBeVisible();
  });
});
