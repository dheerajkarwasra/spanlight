import {
  Activity,
  Beaker,
  BellRing,
  ClipboardCheck,
  Database,
  FileText,
  FlaskConical,
  FlaskRound,
  GitCompareArrows,
  KeyRound,
  LayoutDashboard,
  ListChecks,
  ListTree,
  MessagesSquare,
  PieChart,
  ReceiptText,
  Route,
  Settings,
  ShieldCheck,
  Stethoscope,
  Tags,
  Users,
  Waypoints,
  Wallet,
  type LucideIcon,
} from "lucide-react";

/** Every page a hub tab links to. */
export type NavPath =
  | "/$orgId/$projectId/overview"
  | "/$orgId/$projectId/traces"
  | "/$orgId/$projectId/sessions"
  | "/$orgId/$projectId/users"
  | "/$orgId/$projectId/doctor"
  | "/$orgId/$projectId/providers"
  | "/$orgId/$projectId/releases"
  | "/$orgId/$projectId/alerts"
  | "/$orgId/$projectId/queues"
  | "/$orgId/$projectId/datasets"
  | "/$orgId/$projectId/experiments"
  | "/$orgId/$projectId/evaluators"
  | "/$orgId/$projectId/prompts"
  | "/$orgId/$projectId/playground"
  | "/$orgId/$projectId/attribution"
  | "/$orgId/$projectId/budgets"
  | "/$orgId/$projectId/pricing"
  | "/$orgId/$projectId/reconciliation"
  | "/$orgId/$projectId/gateway"
  | "/$orgId/$projectId/gateway/keys"
  | "/$orgId/$projectId/gateway/routes"
  | "/$orgId/$projectId/gateway/credentials"
  | "/$orgId/$projectId/gateway/lab"
  | "/$orgId/$projectId/settings";

export type HubId =
  "overview" | "traces" | "monitor" | "evaluate" | "prompts" | "cost" | "gateway" | "settings";

export interface HubTab {
  label: string;
  to: NavPath;
  /** The Linear-style "G then <key>" sequence, e.g. "G T". */
  shortcut?: string;
  icon: LucideIcon;
  /** Only the tab's own path makes it active, not its children (Gateway's overview). */
  exact?: boolean;
  /** A live count rendered after the label. */
  badge?: "open-critical";
  /** Extra words the command palette matches. */
  keywords?: string[];
}

export interface Hub {
  id: HubId;
  label: string;
  icon: LucideIcon;
  /** Which run of the rail the hub sits in: above or below the hairline. */
  split: "top" | "bottom";
  /** The rail link goes to the first tab. */
  tabs: [HubTab, ...HubTab[]];
  /** A path under the docs site, for `docsLink`. */
  docs: string;
  badge?: "open-critical";
}

export const HUBS: readonly Hub[] = [
  {
    id: "overview",
    label: "Overview",
    icon: LayoutDashboard,
    split: "top",
    docs: "guides/overview/",
    tabs: [
      {
        label: "Overview",
        to: "/$orgId/$projectId/overview",
        icon: LayoutDashboard,
        shortcut: "G O",
      },
    ],
  },
  {
    id: "traces",
    label: "Traces",
    icon: ListTree,
    split: "top",
    docs: "guides/traces/",
    tabs: [
      { label: "Traces", to: "/$orgId/$projectId/traces", icon: ListTree, shortcut: "G T" },
      {
        label: "Sessions",
        to: "/$orgId/$projectId/sessions",
        icon: MessagesSquare,
        shortcut: "G S",
      },
      { label: "Users", to: "/$orgId/$projectId/users", icon: Users, shortcut: "G U" },
    ],
  },
  {
    id: "monitor",
    label: "Monitor",
    icon: Activity,
    split: "top",
    docs: "doctor/",
    badge: "open-critical",
    tabs: [
      {
        label: "Doctor",
        to: "/$orgId/$projectId/doctor",
        icon: Stethoscope,
        shortcut: "G D",
        badge: "open-critical",
        keywords: ["health", "findings"],
      },
      {
        label: "Provider health",
        to: "/$orgId/$projectId/providers",
        icon: Activity,
        shortcut: "G V",
      },
      {
        label: "Releases",
        to: "/$orgId/$projectId/releases",
        icon: GitCompareArrows,
        shortcut: "G R",
        keywords: ["compare releases"],
      },
      { label: "Alerts", to: "/$orgId/$projectId/alerts", icon: BellRing, shortcut: "G A" },
    ],
  },
  {
    id: "evaluate",
    label: "Evaluate",
    icon: ClipboardCheck,
    split: "top",
    docs: "evaluators/",
    tabs: [
      {
        label: "Review",
        to: "/$orgId/$projectId/queues",
        icon: ClipboardCheck,
        shortcut: "G Q",
        keywords: ["queues", "annotate"],
      },
      { label: "Datasets", to: "/$orgId/$projectId/datasets", icon: Database, shortcut: "G N" },
      {
        label: "Experiments",
        to: "/$orgId/$projectId/experiments",
        icon: FlaskRound,
        shortcut: "G X",
      },
      {
        label: "Evaluators",
        to: "/$orgId/$projectId/evaluators",
        icon: ListChecks,
        shortcut: "G E",
      },
    ],
  },
  {
    id: "prompts",
    label: "Prompts",
    icon: FileText,
    split: "top",
    docs: "prompts/",
    tabs: [
      {
        label: "Prompts",
        to: "/$orgId/$projectId/prompts",
        icon: FileText,
        shortcut: "G P",
        keywords: ["compare prompts", "prompt diff"],
      },
      {
        label: "Playground",
        to: "/$orgId/$projectId/playground",
        icon: FlaskConical,
        shortcut: "G L",
      },
    ],
  },
  {
    id: "cost",
    label: "Cost",
    icon: Wallet,
    split: "bottom",
    docs: "concepts/cost/",
    tabs: [
      {
        label: "Spend",
        to: "/$orgId/$projectId/attribution",
        icon: PieChart,
        shortcut: "G C",
        keywords: ["why is this expensive", "cost", "attribution"],
      },
      { label: "Budgets", to: "/$orgId/$projectId/budgets", icon: Wallet, shortcut: "G B" },
      {
        label: "Reconciliation",
        to: "/$orgId/$projectId/reconciliation",
        icon: ReceiptText,
        keywords: ["check bill", "billing", "invoice", "reconcile"],
      },
      {
        label: "Pricing",
        to: "/$orgId/$projectId/pricing",
        icon: Tags,
        keywords: ["price", "unpriced", "price override", "set prices"],
      },
    ],
  },
  {
    id: "gateway",
    label: "Gateway",
    icon: Waypoints,
    split: "bottom",
    docs: "gateway/quickstart/",
    tabs: [
      {
        label: "Overview",
        to: "/$orgId/$projectId/gateway",
        icon: Waypoints,
        shortcut: "G G",
        exact: true,
      },
      { label: "Keys", to: "/$orgId/$projectId/gateway/keys", icon: KeyRound },
      { label: "Routes", to: "/$orgId/$projectId/gateway/routes", icon: Route },
      { label: "Credentials", to: "/$orgId/$projectId/gateway/credentials", icon: ShieldCheck },
      { label: "Lab", to: "/$orgId/$projectId/gateway/lab", icon: Beaker },
    ],
  },
  {
    id: "settings",
    label: "Settings",
    icon: Settings,
    split: "bottom",
    docs: "guides/access/",
    tabs: [
      { label: "Settings", to: "/$orgId/$projectId/settings", icon: Settings, shortcut: "G ," },
    ],
  },
];

/** Every tab with a G-hotkey, in rail order: the shortcuts, the command palette and help read this. */
export const NAV_ITEMS: readonly (HubTab & { shortcut: string })[] = HUBS.flatMap((hub) =>
  hub.tabs.filter((tab): tab is HubTab & { shortcut: string } => tab.shortcut !== undefined),
);

/**
 * The segments after `/{org}/{project}`. Review work mode (`/annotate/{queue}`) belongs to its
 * queue, so it reads as `/queues/{queue}`; a source's report (`/settings/billing-sources/{id}`)
 * belongs to Cost › Reconciliation, so it reads as `/reconciliation/{id}`. The billing-sources
 * list stays Settings'.
 */
function routeSegments(path: string): string[] {
  const segments = path
    .split("/")
    .filter((segment) => segment !== "")
    .slice(2);
  if (segments[0] === "annotate") {
    return ["queues", ...segments.slice(1)];
  }
  const report =
    segments.length > 2 && segments[0] === "settings" && segments[1] === "billing-sources";
  return report ? ["reconciliation", ...segments.slice(2)] : segments;
}

/** How many segments of `pathname` the tab covers, or 0 when it doesn't match. */
function matchLength(tab: HubTab, segments: string[]): number {
  const own = routeSegments(tab.to);
  const matches = tab.exact
    ? own.length === segments.length && own.every((segment, i) => segment === segments[i])
    : own.every((segment, i) => segment === segments[i]);
  return matches ? own.length : 0;
}

/** The tab for the page at `pathname`: the longest match wins, so `/gateway/keys` is Keys. */
export function activeTab(hub: Hub, pathname: string): HubTab | null {
  const segments = routeSegments(pathname);
  let best: HubTab | null = null;
  let bestLength = 0;
  for (const tab of hub.tabs) {
    const length = matchLength(tab, segments);
    if (length > bestLength) {
      best = tab;
      bestLength = length;
    }
  }
  return best;
}

/**
 * The hub that owns the page at `pathname`, or null outside a project's pages. A reconciliation
 * report (`/settings/billing-sources/{id}`) is Cost's (see `routeSegments`); the rest of
 * `/settings` is Settings', the billing-sources list included.
 */
export function hubForPath(pathname: string): Hub | null {
  const segments = routeSegments(pathname);
  return HUBS.find((hub) => hub.tabs.some((tab) => matchLength(tab, segments) > 0)) ?? null;
}

/** Hub tabs show only on a tab's own page; detail pages show breadcrumbs instead. */
export function showsHubTabs(pathname: string): boolean {
  const hub = hubForPath(pathname);
  if (hub === null || hub.tabs.length < 2) {
    return false;
  }
  const tab = activeTab(hub, pathname);
  return tab !== null && routeSegments(tab.to).length === routeSegments(pathname).length;
}
