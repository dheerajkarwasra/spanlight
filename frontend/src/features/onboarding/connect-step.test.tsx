import type * as Router from "@tanstack/react-router";
import { QueryClient, QueryClientProvider, type UseQueryResult } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import { ME } from "@/test/render-app";
import {
  projectsApi,
  queryKeys,
  type CreatedApiKey,
  type Membership,
  type OnboardingStatus,
  type TraceDetail,
} from "@/lib/api";

import { ConnectStep } from "./connect-step";
import { useOnboardingStatus } from "./use-onboarding-status";

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

function statusOf(
  data: OnboardingStatus & {
    activation?: {
      first_trace: boolean;
      first_trace_id: string | null;
      first_trace_at: string | null;
    };
  },
): UseQueryResult<OnboardingStatus> {
  return {
    data: {
      ...data,
      activation: {
        session: false,
        budget: false,
        evaluation: false,
        first_trace: false,
        first_trace_id: null,
        first_trace_at: null,
        ...data.activation,
      },
    },
    isError: false,
    error: null,
    refetch: vi.fn(),
  } as unknown as UseQueryResult<OnboardingStatus>;
}

const WAITING = statusOf({ has_traces: false, first_trace_at: null });
const ARRIVED = statusOf({
  has_traces: true,
  first_trace_at: FIRST_TRACE_AT,
  activation: { first_trace: true, first_trace_id: TRACE.trace_id, first_trace_at: FIRST_TRACE_AT },
});

function renderStep(status: UseQueryResult<OnboardingStatus>) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ui = (value: UseQueryResult<OnboardingStatus>) => (
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>
          <ConnectStep membership={MEMBERSHIP} project={PROJECT} status={value} />
        </TooltipProvider>
      </QueryClientProvider>
    </StrictMode>
  );
  const view = render(ui(status));
  return { rerender: (value: UseQueryResult<OnboardingStatus>) => view.rerender(ui(value)) };
}

beforeEach(() => {
  vi.spyOn(projectsApi, "trace").mockResolvedValue(TRACE as TraceDetail);
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

    rerender(ARRIVED);
    expect(navigate).toHaveBeenCalledTimes(1);
  });

  it("waits for the authoritative application trace id instead of opening the list", async () => {
    const { rerender } = renderStep(WAITING);
    rerender(
      statusOf({
        has_traces: true,
        first_trace_at: FIRST_TRACE_AT,
        activation: { first_trace: true, first_trace_id: null, first_trace_at: FIRST_TRACE_AT },
      }),
    );
    await screen.findByText("Your app is connected");
    expect(navigate).not.toHaveBeenCalled();
    rerender(ARRIVED);
    await waitFor(() => expect(navigate).toHaveBeenCalledTimes(1));
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
    const card = (await screen.findByText("Your app is connected")).closest("div.flex-col")
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
    vi.spyOn(projectsApi, "trace").mockRejectedValue(new Error("not found"));
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

describe("truthful application connection", () => {
  it("keeps application instructions after setup-only ingestion succeeds", async () => {
    renderStep(
      statusOf({
        has_traces: true,
        first_trace_at: FIRST_TRACE_AT,
        activation: { first_trace: false, first_trace_id: null, first_trace_at: null },
      }),
    );
    expect(await screen.findByText("Test trace received")).toBeInTheDocument();
    expect(
      screen.getByText("Ingestion works. Instrument your app to connect it."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Your app is connected/)).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Open your first trace" })).not.toBeInTheDocument();
    expect(navigate).not.toHaveBeenCalled();
  });

  it("shows a failed refresh despite cached waiting data and preserves the snippet", async () => {
    const { rerender } = renderStep(WAITING);
    await userEvent.click(screen.getByRole("tab", { name: "curl" }));
    rerender({
      ...WAITING,
      isError: true,
      error: new Error("offline"),
    } as UseQueryResult<OnboardingStatus>);
    expect(await screen.findByRole("alert")).toHaveTextContent("Checking interrupted");
    expect(screen.getByRole("alert")).toHaveTextContent(
      "We couldn't refresh. Your key and snippets are still here.",
    );
    expect(screen.getByRole("button", { name: "Try again" })).toBeEnabled();
    expect(screen.getByRole("tab", { name: "curl" })).toHaveAttribute("aria-selected", "true");
  });

  it("marks the known part of a first application's cost as partial", async () => {
    vi.spyOn(projectsApi, "trace").mockResolvedValue({
      ...TRACE,
      cost_usd: "0.0021",
      has_unpriced: true,
    } as TraceDetail);
    renderStep(ARRIVED);
    const chips = await screen.findByRole("list", { name: "First trace" });
    expect(chips).toHaveTextContent("≥ $0.002100 · partial");
    expect(within(chips).getByRole("img", { name: /total is a lower bound/ })).toBeInTheDocument();
  });

  it("shows an unknown first application's cost instead of omitting it", async () => {
    renderStep(ARRIVED);
    const chips = await screen.findByRole("list", { name: "First trace" });
    expect(chips).toHaveTextContent("Cost: —");
    expect(
      screen.getByText("Application trace received. Cost is unavailable for this trace."),
    ).toBeInTheDocument();
  });
});

describe("onboarding connection cache", () => {
  it("keeps dashboard cache data from labeling an application as setup-only", async () => {
    const dashboardStatus = { has_traces: true, first_trace_at: FIRST_TRACE_AT };
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 30_000 } },
    });
    queryClient.setQueryData(queryKeys.project(PROJECT.id).onboarding, dashboardStatus);
    vi.spyOn(projectsApi, "onboarding").mockResolvedValue(dashboardStatus);
    vi.spyOn(projectsApi, "activation").mockResolvedValue({
      first_trace: true,
      first_trace_id: TRACE.trace_id,
      first_trace_at: FIRST_TRACE_AT,
      session: false,
      budget: false,
      evaluation: false,
    });
    function PollingStep() {
      const status = useOnboardingStatus(PROJECT.id);
      return <ConnectStep membership={MEMBERSHIP} project={PROJECT} status={status} />;
    }
    render(
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>
          <PollingStep />
        </TooltipProvider>
      </QueryClientProvider>,
    );
    expect(screen.queryByText("Test trace received")).not.toBeInTheDocument();
    expect(screen.getByText("Waiting for an application trace…")).toBeInTheDocument();
    expect(
      await screen.findByRole("heading", { name: "Your app is connected" }),
    ).toBeInTheDocument();
    expect(queryClient.getQueryData(queryKeys.project(PROJECT.id).onboarding)).toEqual(
      dashboardStatus,
    );
  });
});
