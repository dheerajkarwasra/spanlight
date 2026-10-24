import type * as Router from "@tanstack/react-router";
import { QueryClient, QueryClientProvider, type UseQueryResult } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ME } from "@/test/render-app";
import {
  projectsApi,
  type CreatedApiKey,
  type Membership,
  type OnboardingStatus,
  type TraceSummary,
} from "@/lib/api";

import { ConnectStep } from "./connect-step";

const navigate = vi.fn();
const toastSuccess = vi.fn();

vi.mock("@tanstack/react-router", async (importOriginal) => ({
  ...(await importOriginal<typeof Router>()),
  useNavigate: () => navigate,
  Link: ({
    children,
    to,
    params,
    className,
  }: {
    children: ReactNode;
    to: string;
    params?: Record<string, string>;
    className?: string;
  }) => (
    <a
      className={className}
      href={to.replace(/\$(\w+)/g, (_, name: string) => params?.[name] ?? name)}
    >
      {children}
    </a>
  ),
}));

vi.mock("sonner", () => ({
  toast: {
    success: (message: string) => {
      toastSuccess(message);
    },
  },
}));

vi.mock("@/features/auth", () => ({ useMe: () => ME }));

const MEMBERSHIP = ME.memberships[0] as Membership;
const PROJECT = { id: "proj_1", name: "Support bot" } as Parameters<
  typeof ConnectStep
>[0]["project"];
const FIRST_TRACE_AT = "2026-10-08T14:32:07.123456+00:00";
const TRACE = { trace_id: "a".repeat(32), name: "chat", duration_ms: 1200, cost_usd: null };

function statusOf(data: OnboardingStatus): UseQueryResult<OnboardingStatus> {
  return {
    data,
    isError: false,
    error: null,
    refetch: vi.fn(),
  } as unknown as UseQueryResult<OnboardingStatus>;
}

const WAITING = statusOf({ has_traces: false, first_trace_at: null });
const ARRIVED = statusOf({ has_traces: true, first_trace_at: FIRST_TRACE_AT });

function renderStep(status: UseQueryResult<OnboardingStatus>) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ui = (value: UseQueryResult<OnboardingStatus>) => (
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <ConnectStep membership={MEMBERSHIP} project={PROJECT} status={value} />
      </QueryClientProvider>
    </StrictMode>
  );
  const view = render(ui(status));
  return { rerender: (value: UseQueryResult<OnboardingStatus>) => view.rerender(ui(value)) };
}

beforeEach(() => {
  vi.spyOn(projectsApi, "traces").mockResolvedValue({
    items: [TRACE as TraceSummary],
    next_cursor: null,
  });
});

afterEach(() => {
  vi.restoreAllMocks();
  navigate.mockReset();
  toastSuccess.mockReset();
});

describe("ConnectStep first trace", () => {
  it("opens the first trace once, replacing the onboarding entry, when no secret is shown", async () => {
    const { rerender } = renderStep(WAITING);
    rerender(ARRIVED);

    await waitFor(() => {
      expect(navigate).toHaveBeenCalledTimes(1);
    });
    expect(navigate).toHaveBeenCalledWith({
      to: "/$orgId/$projectId/traces/$traceId",
      params: { orgId: MEMBERSHIP.org.id, projectId: PROJECT.id, traceId: TRACE.trace_id },
      replace: true,
    });
    expect(toastSuccess).toHaveBeenCalledExactlyOnceWith("Your first trace arrived.");

    rerender(statusOf({ has_traces: true, first_trace_at: FIRST_TRACE_AT }));
    expect(navigate).toHaveBeenCalledTimes(1);
  });

  it("waits for the first trace's id instead of opening the list", async () => {
    let resolve: (page: { items: TraceSummary[]; next_cursor: null }) => void = () => {};
    vi.spyOn(projectsApi, "traces").mockReturnValue(
      new Promise((done) => {
        resolve = done;
      }),
    );
    const { rerender } = renderStep(WAITING);
    rerender(ARRIVED);
    await screen.findByText("First trace received");
    expect(navigate).not.toHaveBeenCalled();

    resolve({ items: [TRACE as TraceSummary], next_cursor: null });
    await waitFor(() => {
      expect(navigate).toHaveBeenCalledTimes(1);
    });
  });

  it("offers the trace and keeps the key card while a new secret is on screen", async () => {
    const created: CreatedApiKey = {
      id: "k1",
      name: "Onboarding key",
      prefix: "spl_live_AAAA",
      secret: "spl_live_AAAA_secret",
      scopes: [],
      created_by: null,
      created_at: "2026-10-08T14:00:00Z",
      last_used_at: null,
      expires_at: null,
      revoked_at: null,
    };
    vi.spyOn(projectsApi, "createKey").mockResolvedValue(created);
    const { rerender } = renderStep(WAITING);
    await userEvent.click(screen.getByRole("button", { name: "Create API key" }));
    await screen.findByRole("button", { name: "Copy API key" });

    rerender(ARRIVED);
    const card = (await screen.findByText("First trace received")).closest("div.flex-col")
      ?.parentElement?.parentElement as HTMLElement;
    const link = await within(card).findByRole("link", { name: "Open your first trace" });
    expect(link).toHaveAttribute(
      "href",
      `/${MEMBERSHIP.org.id}/${PROJECT.id}/traces/${TRACE.trace_id}`,
    );
    expect(within(card).getAllByRole("link")).toHaveLength(1);
    expect(screen.queryByRole("link", { name: "View traces" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Go to overview" })).toBeNull();
    expect(screen.getByRole("button", { name: "Copy API key" })).toBeInTheDocument();
    expect(navigate).not.toHaveBeenCalled();
    expect(toastSuccess).not.toHaveBeenCalledWith("Your first trace arrived.");
  });

  it("links to the trace list when the first trace can't be found", async () => {
    vi.spyOn(projectsApi, "traces").mockResolvedValue({ items: [], next_cursor: null });
    renderStep(ARRIVED);
    const link = await screen.findByRole("link", { name: "View traces" });
    expect(link).toHaveAttribute("href", `/${MEMBERSHIP.org.id}/${PROJECT.id}/traces`);
    expect(screen.queryByRole("link", { name: "Open your first trace" })).toBeNull();
    expect(navigate).not.toHaveBeenCalled();
  });

  it("doesn't open a trace that was already there when the page opened", async () => {
    renderStep(ARRIVED);
    const link = await screen.findByRole("link", { name: "Open your first trace" });
    expect(link).toHaveAttribute(
      "href",
      `/${MEMBERSHIP.org.id}/${PROJECT.id}/traces/${TRACE.trace_id}`,
    );
    expect(navigate).not.toHaveBeenCalled();
    expect(toastSuccess).not.toHaveBeenCalled();
  });

  it("still offers a new API key to a project that was already connected", async () => {
    renderStep(ARRIVED);
    await screen.findByRole("link", { name: "Open your first trace" });
    expect(screen.getByRole("button", { name: "Create API key" })).toBeInTheDocument();
  });
});
