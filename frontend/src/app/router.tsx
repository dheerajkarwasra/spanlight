import type { QueryClient } from "@tanstack/react-query";
import {
  createRootRouteWithContext,
  createRoute,
  createRouter,
  lazyRouteComponent,
  Outlet,
  redirect,
  retainSearchParams,
} from "@tanstack/react-router";

import { bindSlugSource, ensureCanonicalProjectUrl, projectParams } from "@/app/project-params";
import { NotFoundPage, RootLayout, RouteErrorPage } from "@/app/route-pages";
import { LoginPage } from "@/features/auth/login-page";
import { ensureMe } from "@/features/auth/queries";
import { authSearchSchema, safeNextPath } from "@/features/auth/search";
import { SignupPage } from "@/features/auth/signup-page";
import { TwoFactorPage } from "@/features/auth/two-factor-page";
import { attributionSearchSchema } from "@/features/attribution/search";
import { budgetsSearchSchema } from "@/features/budgets/budget-prefill";
import { doctorSearchSchema } from "@/features/doctor/search";
import {
  compareSearchSchema,
  experimentSearchSchema,
  newExperimentSearchSchema,
} from "@/features/experiments/search";
import { onboardingSearchSchema } from "@/features/onboarding/search";
import { playgroundSearchSchema } from "@/features/playground/search";
import { pricingSearchSchema } from "@/features/pricing/price-override-form";
import { promptDetailSearchSchema, promptsSearchSchema } from "@/features/prompts/search";
import { releasesSearchSchema } from "@/features/releases/search";
import { AppShell } from "@/features/shell/app-shell";
import { resolveHomeDestination } from "@/features/shell/home-redirect";
import { reconciliationSearchSchema } from "@/features/billing-sources/reconciliation-search";
import { auditSearchSchema } from "@/features/settings/audit-search";
import { securitySearchSchema } from "@/features/settings/security/search";
import { sessionsSearchSchema } from "@/features/sessions/sessions-search";
import {
  traceCompareSearchSchema,
  traceDetailSearchSchema,
  traceFiltersSchema,
} from "@/features/traces/search";
import { usersSearchSchema } from "@/features/users/search";
import { projectSearchSchema } from "@/lib/time-range";

/*
 * Public pages and the app shell load eagerly; every page inside the shell is
 * code-split so the first paint doesn't pay for charts or the trace viewer.
 */

export interface RouterContext {
  queryClient: QueryClient;
}

/**
 * Each route names its page in the tab: "<Page> · Spanlight". Routes only set the title;
 * the description and social meta tags stay static in index.html.
 */
function pageTitle(page: string) {
  return { meta: [{ title: `${page} · Spanlight` }] };
}

const rootRoute = createRootRouteWithContext<RouterContext>()({
  // Every page names itself, so the root's title only shows when no route matched the URL.
  head: () => pageTitle("Page not found"),
  component: RootLayout,
  notFoundComponent: NotFoundPage,
  errorComponent: RouteErrorPage,
});

/* ---------- Public routes ---------- */

/**
 * "/" is the marketing page for visitors; signed-in users go straight to their last project
 * (or onboarding), so they never load the landing chunk.
 */
const landingRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  beforeLoad: async ({ context }) => {
    const me = await ensureMe(context.queryClient);
    if (me) {
      throw redirect(await resolveHomeDestination(context.queryClient, me));
    }
  },
  // The full title from index.html; without it the root's "Page not found" would show here.
  head: () => ({
    meta: [{ title: "Spanlight — Open-source LLM observability" }],
  }),
  component: lazyRouteComponent(() => import("@/features/landing/landing-page"), "LandingPage"),
});

const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/login",
  validateSearch: authSearchSchema,
  beforeLoad: async ({ context, search }) => {
    const me = await ensureMe(context.queryClient);
    if (me) {
      throw redirect({ href: safeNextPath(search.next) ?? "/" });
    }
  },
  head: () => pageTitle("Sign in"),
  component: LoginPage,
});

const signupRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/signup",
  validateSearch: authSearchSchema,
  beforeLoad: async ({ context, search }) => {
    const me = await ensureMe(context.queryClient);
    if (me) {
      throw redirect({ href: safeNextPath(search.next) ?? "/" });
    }
  },
  head: () => pageTitle("Create an account"),
  component: SignupPage,
});

/** The second step of signing in. The challenge is never in this URL; see `login-challenge.ts`. */
const twoFactorRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/login/two-factor",
  validateSearch: authSearchSchema,
  beforeLoad: async ({ context, search }) => {
    const me = await ensureMe(context.queryClient);
    if (me) {
      throw redirect({ href: safeNextPath(search.next) ?? "/" });
    }
  },
  head: () => pageTitle("Two-factor sign-in"),
  component: TwoFactorPage,
});

/*
 * The three account-recovery pages are open to everyone, signed in or not: a forgotten password,
 * a reset link and a verification link are all opened by people who may have no session. Their
 * secrets arrive in the URL fragment (see `fragment-token.ts`).
 */
const forgotPasswordRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/forgot-password",
  head: () => pageTitle("Reset your password"),
  component: lazyRouteComponent(
    () => import("@/features/auth/forgot-password-page"),
    "ForgotPasswordPage",
  ),
});

const resetPasswordRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/reset-password",
  head: () => pageTitle("Choose a new password"),
  component: lazyRouteComponent(
    () => import("@/features/auth/reset-password-page"),
    "ResetPasswordPage",
  ),
});

const verifyEmailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/verify-email",
  head: () => pageTitle("Verify your email"),
  component: lazyRouteComponent(
    () => import("@/features/auth/verify-email-page"),
    "VerifyEmailPage",
  ),
});

const inviteRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/invite/$token",
  beforeLoad: async ({ context }) => ({ me: await ensureMe(context.queryClient) }),
  head: () => pageTitle("Invitation"),
  component: lazyRouteComponent(() => import("@/features/auth/invite-page"), "InvitePage"),
});

/* ---------- Authenticated routes ---------- */

const authedRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: "_authed",
  beforeLoad: async ({ context, location }) => {
    const me = await ensureMe(context.queryClient);
    if (!me) {
      throw redirect({ to: "/login", search: { next: location.href } });
    }
    return { me };
  },
  component: Outlet,
});

const onboardingRoute = createRoute({
  getParentRoute: () => authedRoute,
  path: "/onboarding",
  validateSearch: onboardingSearchSchema,
  head: () => pageTitle("Setup"),
  component: lazyRouteComponent(
    () => import("@/features/onboarding/onboarding-page"),
    "OnboardingPage",
  ),
});

/**
 * The Security page with no project around it. Where an organization requires two-factor
 * authentication and the person has none, every project answers 403, so this is the one page that
 * always opens, and the place home sends them (see `resolveHomeDestination`).
 */
const accountSecurityRoute = createRoute({
  getParentRoute: () => authedRoute,
  path: "/account/security",
  validateSearch: securitySearchSchema,
  head: () => pageTitle("Security"),
  component: lazyRouteComponent(
    () => import("@/features/settings/security/account-security-page"),
    "AccountSecurityPage",
  ),
});

const projectRoute = createRoute({
  getParentRoute: () => authedRoute,
  path: "/$orgId/$projectId",
  // The URL carries slugs; every page below reads ids (see `project-params.ts`).
  params: projectParams,
  validateSearch: projectSearchSchema,
  search: {
    middlewares: [retainSearchParams(["range", "from", "to", "env"])],
  },
  beforeLoad: ({ context, params, location }) =>
    ensureCanonicalProjectUrl({
      queryClient: context.queryClient,
      me: context.me,
      params,
      location,
    }),
  component: AppShell,
});

const projectIndexRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: "/",
  beforeLoad: ({ params }) => {
    throw redirect({ to: "/$orgId/$projectId/overview", params });
  },
});

const overviewRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: "/overview",
  head: () => pageTitle("Overview"),
  component: lazyRouteComponent(() => import("@/features/overview/overview-page"), "OverviewPage"),
});

const tracesRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: "/traces",
  validateSearch: traceFiltersSchema,
  head: () => pageTitle("Traces"),
  component: lazyRouteComponent(() => import("@/features/traces/traces-page"), "TracesPage"),
});

const traceDetailRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: "/traces/$traceId",
  validateSearch: traceDetailSearchSchema,
  head: () => pageTitle("Trace"),
  component: lazyRouteComponent(
    () => import("@/features/traces/trace-detail-page"),
    "TraceDetailPage",
  ),
});

const traceCompareRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: "/traces/compare",
  validateSearch: traceCompareSearchSchema,
  head: () => pageTitle("Compare traces"),
  component: lazyRouteComponent(
    () => import("@/features/traces/trace-compare-page"),
    "TraceComparePage",
  ),
});

const sessionsRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: "/sessions",
  validateSearch: sessionsSearchSchema,
  head: () => pageTitle("Sessions"),
  component: lazyRouteComponent(() => import("@/features/sessions/sessions-page"), "SessionsPage"),
});

const sessionDetailRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: "/sessions/$sessionId",
  head: () => pageTitle("Session"),
  component: lazyRouteComponent(
    () => import("@/features/sessions/session-detail-page"),
    "SessionDetailPage",
  ),
});

const doctorRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: "/doctor",
  validateSearch: doctorSearchSchema,
  head: () => pageTitle("Doctor"),
  component: lazyRouteComponent(() => import("@/features/doctor/doctor-page"), "DoctorPage"),
});

const insightDetailRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: "/doctor/$insightId",
  head: () => pageTitle("Insight"),
  component: lazyRouteComponent(
    () => import("@/features/doctor/insight-detail-page"),
    "InsightDetailPage",
  ),
});

const providersRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: "/providers",
  head: () => pageTitle("Provider health"),
  component: lazyRouteComponent(
    () => import("@/features/providers/providers-page"),
    "ProvidersPage",
  ),
});

const releasesRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: "/releases",
  validateSearch: releasesSearchSchema,
  head: () => pageTitle("Releases"),
  component: lazyRouteComponent(() => import("@/features/releases/releases-page"), "ReleasesPage"),
});

const usersRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: "/users",
  validateSearch: usersSearchSchema,
  head: () => pageTitle("Users"),
  component: lazyRouteComponent(() => import("@/features/users/users-page"), "UsersPage"),
});

const userDetailRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: "/users/$userId",
  head: () => pageTitle("User"),
  component: lazyRouteComponent(
    () => import("@/features/users/user-detail-page"),
    "UserDetailPage",
  ),
});

const attributionRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: "/attribution",
  validateSearch: attributionSearchSchema,
  head: () => pageTitle("Spend"),
  component: lazyRouteComponent(
    () => import("@/features/attribution/attribution-page"),
    "AttributionPage",
  ),
});

const gatewayRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: "/gateway",
  head: () => pageTitle("Gateway"),
  component: lazyRouteComponent(
    () => import("@/features/gateway/overview-page"),
    "GatewayOverviewPage",
  ),
});

const gatewayKeysRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: "/gateway/keys",
  head: () => pageTitle("Gateway keys"),
  component: lazyRouteComponent(
    () => import("@/features/gateway/keys/keys-page"),
    "GatewayKeysPage",
  ),
});

const gatewayRoutesRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: "/gateway/routes",
  head: () => pageTitle("Gateway routes"),
  component: lazyRouteComponent(
    () => import("@/features/gateway/routes/routes-page"),
    "GatewayRoutesPage",
  ),
});

const gatewayRouteEditorRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: "/gateway/routes/$routeId",
  head: () => pageTitle("Gateway route"),
  component: lazyRouteComponent(
    () => import("@/features/gateway/routes/route-editor-page"),
    "RouteEditorPage",
  ),
});

const gatewayCredentialsRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: "/gateway/credentials",
  head: () => pageTitle("Provider credentials"),
  component: lazyRouteComponent(
    () => import("@/features/gateway/credentials/credentials-page"),
    "GatewayCredentialsPage",
  ),
});

const gatewayLabRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: "/gateway/lab",
  head: () => pageTitle("Integration Lab"),
  component: lazyRouteComponent(() => import("@/features/gateway/lab/lab-page"), "GatewayLabPage"),
});

const promptsRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: "/prompts",
  validateSearch: promptsSearchSchema,
  head: () => pageTitle("Prompts"),
  component: lazyRouteComponent(() => import("@/features/prompts/prompts-page"), "PromptsPage"),
});

/** `$name` is the prompt name with its slashes, escaped like the API path (`toPathPromptName`). */
const promptDetailRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: "/prompts/$name",
  validateSearch: promptDetailSearchSchema,
  head: () => pageTitle("Prompt"),
  component: lazyRouteComponent(
    () => import("@/features/prompts/prompt-detail-page"),
    "PromptDetailPage",
  ),
});

const playgroundRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: "/playground",
  validateSearch: playgroundSearchSchema,
  head: () => pageTitle("Playground"),
  component: lazyRouteComponent(
    () => import("@/features/playground/playground-page"),
    "PlaygroundPage",
  ),
});

const datasetsRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: "/datasets",
  head: () => pageTitle("Datasets"),
  component: lazyRouteComponent(() => import("@/features/datasets/datasets-page"), "DatasetsPage"),
});

const datasetDetailRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: "/datasets/$datasetId",
  head: () => pageTitle("Dataset"),
  component: lazyRouteComponent(
    () => import("@/features/datasets/dataset-detail-page"),
    "DatasetDetailPage",
  ),
});

const queuesRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: "/queues",
  head: () => pageTitle("Review"),
  component: lazyRouteComponent(() => import("@/features/annotations/queues-page"), "QueuesPage"),
});

const queueDetailRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: "/queues/$queueId",
  head: () => pageTitle("Review queue"),
  component: lazyRouteComponent(
    () => import("@/features/annotations/queue-detail-page"),
    "QueueDetailPage",
  ),
});

/** Work mode: the app shell drops its rail and top bar on this route (see `AppShell`). */
const annotateRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: "/annotate/$queueId",
  head: () => pageTitle("Reviewing"),
  component: lazyRouteComponent(
    () => import("@/features/annotations/annotate-page"),
    "AnnotatePage",
  ),
});

const experimentsRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: "/experiments",
  head: () => pageTitle("Experiments"),
  component: lazyRouteComponent(
    () => import("@/features/experiments/experiments-page"),
    "ExperimentsPage",
  ),
});

const newExperimentRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: "/experiments/new",
  validateSearch: newExperimentSearchSchema,
  head: () => pageTitle("New experiment"),
  component: lazyRouteComponent(
    () => import("@/features/experiments/new-experiment-page"),
    "NewExperimentPage",
  ),
});

/** `?a=&b=` are the runs ticked for comparison. */
const experimentDetailRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: "/experiments/$experimentId",
  validateSearch: experimentSearchSchema,
  head: () => pageTitle("Experiment"),
  component: lazyRouteComponent(
    () => import("@/features/experiments/experiment-detail-page"),
    "ExperimentDetailPage",
  ),
});

/** `?a=` baseline run, `?b=` candidate run, `?score=` the score the items sort by. */
const experimentCompareRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: "/experiments/$experimentId/compare",
  validateSearch: compareSearchSchema,
  head: () => pageTitle("Compare runs"),
  component: lazyRouteComponent(() => import("@/features/experiments/compare-page"), "ComparePage"),
});

const experimentRunRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: "/experiments/$experimentId/runs/$runId",
  head: () => pageTitle("Experiment run"),
  component: lazyRouteComponent(() => import("@/features/experiments/run-detail"), "RunDetailPage"),
});

const evaluatorsRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: "/evaluators",
  head: () => pageTitle("Evaluators"),
  component: lazyRouteComponent(
    () => import("@/features/evaluators/evaluators-page"),
    "EvaluatorsPage",
  ),
});

const newEvaluatorRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: "/evaluators/new",
  head: () => pageTitle("New evaluator"),
  component: lazyRouteComponent(
    () => import("@/features/evaluators/evaluator-form-page"),
    "NewEvaluatorPage",
  ),
});

const evaluatorDetailRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: "/evaluators/$evaluatorId",
  head: () => pageTitle("Evaluator"),
  component: lazyRouteComponent(
    () => import("@/features/evaluators/evaluator-detail-page"),
    "EvaluatorDetailPage",
  ),
});

const editEvaluatorRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: "/evaluators/$evaluatorId/edit",
  head: () => pageTitle("Edit evaluator"),
  component: lazyRouteComponent(
    () => import("@/features/evaluators/evaluator-form-page"),
    "EditEvaluatorPage",
  ),
});

const alertsRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: "/alerts",
  head: () => pageTitle("Alerts"),
  component: lazyRouteComponent(() => import("@/features/alerts/alerts-page"), "AlertsPage"),
});

/** Channels moved to Settings; old links and bookmarks still land there. */
const alertChannelsRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: "/alerts/channels",
  beforeLoad: ({ params, search }) => {
    throw redirect({ to: "/$orgId/$projectId/settings/channels", params, search });
  },
});

const alertRuleRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: "/alerts/$ruleId",
  head: () => pageTitle("Alert rule"),
  component: lazyRouteComponent(
    () => import("@/features/alerts/rule-detail-page"),
    "AlertRuleDetailPage",
  ),
});

const budgetsRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: "/budgets",
  validateSearch: budgetsSearchSchema,
  head: () => pageTitle("Budgets"),
  component: lazyRouteComponent(() => import("@/features/budgets/budgets-page"), "BudgetsPage"),
});

const pricingRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: "/pricing",
  validateSearch: pricingSearchSchema,
  head: () => pageTitle("Pricing"),
  component: lazyRouteComponent(() => import("@/features/pricing/pricing-page"), "PricingPage"),
});

/** Cost › Reconciliation: the billing sources that map this project; each opens its report. */
const reconciliationTabRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: "/reconciliation",
  head: () => pageTitle("Reconciliation"),
  component: lazyRouteComponent(
    () => import("@/features/billing-sources/reconciliation-tab-page"),
    "ReconciliationTabPage",
  ),
});

const settingsRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: "/settings",
  component: lazyRouteComponent(
    () => import("@/features/settings/settings-layout"),
    "SettingsLayout",
  ),
});

const settingsIndexRoute = createRoute({
  getParentRoute: () => settingsRoute,
  path: "/",
  beforeLoad: ({ params }) => {
    throw redirect({ to: "/$orgId/$projectId/settings/project", params });
  },
});

const projectSettingsRoute = createRoute({
  getParentRoute: () => settingsRoute,
  path: "/project",
  head: () => pageTitle("Project settings"),
  component: lazyRouteComponent(
    () => import("@/features/settings/project-settings-page"),
    "ProjectSettingsPage",
  ),
});

const apiKeysRoute = createRoute({
  getParentRoute: () => settingsRoute,
  path: "/keys",
  head: () => pageTitle("API keys"),
  component: lazyRouteComponent(() => import("@/features/settings/api-keys-page"), "ApiKeysPage"),
});

const exportsRoute = createRoute({
  getParentRoute: () => settingsRoute,
  path: "/exports",
  head: () => pageTitle("Exports"),
  component: lazyRouteComponent(() => import("@/features/settings/exports-page"), "ExportsPage"),
});

const organizationRoute = createRoute({
  getParentRoute: () => settingsRoute,
  path: "/organization",
  head: () => pageTitle("Organization settings"),
  component: lazyRouteComponent(
    () => import("@/features/settings/organization/organization-page"),
    "OrganizationPage",
  ),
});

const membersRoute = createRoute({
  getParentRoute: () => settingsRoute,
  path: "/members",
  head: () => pageTitle("Members"),
  component: lazyRouteComponent(() => import("@/features/settings/members-page"), "MembersPage"),
});

const settingsChannelsRoute = createRoute({
  getParentRoute: () => settingsRoute,
  path: "/channels",
  head: () => pageTitle("Notification channels"),
  component: lazyRouteComponent(
    () => import("@/features/alerts/channels-page"),
    "AlertChannelsPage",
  ),
});

const billingSourcesRoute = createRoute({
  getParentRoute: () => settingsRoute,
  path: "/billing-sources",
  head: () => pageTitle("Billing sources"),
  component: lazyRouteComponent(
    () => import("@/features/billing-sources/billing-sources-page"),
    "BillingSourcesPage",
  ),
});

/**
 * A billing source's reconciliation. A sibling of the settings layout, not its child: the page has
 * its own header and back link instead of the settings navigation (Figma "Reconciliation / *").
 */
const reconciliationRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: "/settings/billing-sources/$sourceId",
  validateSearch: reconciliationSearchSchema,
  head: () => pageTitle("Reconciliation"),
  component: lazyRouteComponent(
    () => import("@/features/billing-sources/reconciliation-page"),
    "ReconciliationPage",
  ),
});

const auditRoute = createRoute({
  getParentRoute: () => settingsRoute,
  path: "/audit",
  validateSearch: auditSearchSchema,
  head: () => pageTitle("Audit log"),
  component: lazyRouteComponent(() => import("@/features/settings/audit-log-page"), "AuditLogPage"),
});

const accountRoute = createRoute({
  getParentRoute: () => settingsRoute,
  path: "/account",
  head: () => pageTitle("Account"),
  component: lazyRouteComponent(() => import("@/features/settings/account-page"), "AccountPage"),
});

const securityRoute = createRoute({
  getParentRoute: () => settingsRoute,
  path: "/security",
  validateSearch: securitySearchSchema,
  head: () => pageTitle("Security"),
  component: lazyRouteComponent(
    () => import("@/features/settings/security/security-page"),
    "SecurityPage",
  ),
});

const tokensRoute = createRoute({
  getParentRoute: () => settingsRoute,
  path: "/tokens",
  head: () => pageTitle("Tokens"),
  component: lazyRouteComponent(
    () => import("@/features/settings/tokens/tokens-page"),
    "TokensPage",
  ),
});

const routeTree = rootRoute.addChildren([
  landingRoute,
  loginRoute,
  twoFactorRoute,
  signupRoute,
  forgotPasswordRoute,
  resetPasswordRoute,
  verifyEmailRoute,
  inviteRoute,
  authedRoute.addChildren([
    onboardingRoute,
    accountSecurityRoute,
    projectRoute.addChildren([
      projectIndexRoute,
      overviewRoute,
      tracesRoute,
      traceCompareRoute,
      traceDetailRoute,
      sessionsRoute,
      sessionDetailRoute,
      doctorRoute,
      insightDetailRoute,
      providersRoute,
      releasesRoute,
      usersRoute,
      userDetailRoute,
      attributionRoute,
      gatewayRoute,
      gatewayKeysRoute,
      gatewayRoutesRoute,
      gatewayRouteEditorRoute,
      gatewayCredentialsRoute,
      gatewayLabRoute,
      promptsRoute,
      promptDetailRoute,
      playgroundRoute,
      datasetsRoute,
      datasetDetailRoute,
      queuesRoute,
      queueDetailRoute,
      annotateRoute,
      evaluatorsRoute,
      newEvaluatorRoute,
      evaluatorDetailRoute,
      editEvaluatorRoute,
      experimentsRoute,
      newExperimentRoute,
      experimentDetailRoute,
      experimentCompareRoute,
      experimentRunRoute,
      alertsRoute,
      alertChannelsRoute,
      alertRuleRoute,
      budgetsRoute,
      pricingRoute,
      reconciliationTabRoute,
      reconciliationRoute,
      settingsRoute.addChildren([
        settingsIndexRoute,
        projectSettingsRoute,
        apiKeysRoute,
        exportsRoute,
        organizationRoute,
        membersRoute,
        settingsChannelsRoute,
        billingSourcesRoute,
        auditRoute,
        accountRoute,
        securityRoute,
        tokensRoute,
      ]),
    ]),
  ]),
]);

export function createAppRouter(queryClient: QueryClient) {
  bindSlugSource(queryClient);
  return createRouter({
    routeTree,
    context: { queryClient },
    defaultPreload: "intent",
    defaultPreloadStaleTime: 0,
    scrollRestoration: true,
  });
}

declare module "@tanstack/react-router" {
  interface Register {
    router: ReturnType<typeof createAppRouter>;
  }
}
