import {
  Activity,
  BellRing,
  ClipboardCheck,
  Database,
  FileText,
  FlaskConical,
  FlaskRound,
  GitCompareArrows,
  LayoutDashboard,
  ListChecks,
  ListTree,
  MessagesSquare,
  PieChart,
  Settings,
  Stethoscope,
  Users,
  Waypoints,
  Wallet,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  label: string;
  to:
    | "/$orgId/$projectId/overview"
    | "/$orgId/$projectId/traces"
    | "/$orgId/$projectId/sessions"
    | "/$orgId/$projectId/doctor"
    | "/$orgId/$projectId/providers"
    | "/$orgId/$projectId/releases"
    | "/$orgId/$projectId/users"
    | "/$orgId/$projectId/attribution"
    | "/$orgId/$projectId/gateway"
    | "/$orgId/$projectId/alerts"
    | "/$orgId/$projectId/budgets"
    | "/$orgId/$projectId/prompts"
    | "/$orgId/$projectId/playground"
    | "/$orgId/$projectId/datasets"
    | "/$orgId/$projectId/queues"
    | "/$orgId/$projectId/evaluators"
    | "/$orgId/$projectId/experiments"
    | "/$orgId/$projectId/settings";
  icon: LucideIcon;
  shortcut: string;
  /** A live count rendered after the label in the rail. */
  badge?: "open-critical";
}

export interface NavGroup {
  /** Shown above the group in the rail; null for the plain, unlabelled run of pages. */
  heading: string | null;
  items: NavItem[];
}

/** The Improve pages: prompts, playground, datasets, review queues, evaluators, experiments. */
const IMPROVE_ITEMS: NavItem[] = [
  { label: "Prompts", to: "/$orgId/$projectId/prompts", icon: FileText, shortcut: "G P" },
  {
    label: "Playground",
    to: "/$orgId/$projectId/playground",
    icon: FlaskConical,
    shortcut: "G L",
  },
  { label: "Datasets", to: "/$orgId/$projectId/datasets", icon: Database, shortcut: "G N" },
  { label: "Review", to: "/$orgId/$projectId/queues", icon: ClipboardCheck, shortcut: "G Q" },
  {
    label: "Evaluators",
    to: "/$orgId/$projectId/evaluators",
    icon: ListChecks,
    shortcut: "G E",
  },
  {
    label: "Experiments",
    to: "/$orgId/$projectId/experiments",
    icon: FlaskRound,
    shortcut: "G X",
  },
];

export const NAV_GROUPS: NavGroup[] = [
  {
    heading: null,
    items: [
      {
        label: "Overview",
        to: "/$orgId/$projectId/overview",
        icon: LayoutDashboard,
        shortcut: "G O",
      },
      { label: "Traces", to: "/$orgId/$projectId/traces", icon: ListTree, shortcut: "G T" },
      {
        label: "Sessions",
        to: "/$orgId/$projectId/sessions",
        icon: MessagesSquare,
        shortcut: "G S",
      },
    ],
  },
  {
    heading: "Monitor",
    items: [
      {
        label: "Doctor",
        to: "/$orgId/$projectId/doctor",
        icon: Stethoscope,
        shortcut: "G D",
        badge: "open-critical",
      },
      {
        label: "Provider health",
        to: "/$orgId/$projectId/providers",
        icon: Activity,
        shortcut: "G V",
      },
      { label: "Alerts", to: "/$orgId/$projectId/alerts", icon: BellRing, shortcut: "G A" },
      { label: "Budgets", to: "/$orgId/$projectId/budgets", icon: Wallet, shortcut: "G B" },
    ],
  },
  {
    heading: "Analyze",
    items: [
      {
        label: "Releases",
        to: "/$orgId/$projectId/releases",
        icon: GitCompareArrows,
        shortcut: "G R",
      },
      { label: "Users", to: "/$orgId/$projectId/users", icon: Users, shortcut: "G U" },
      {
        label: "Attribution",
        to: "/$orgId/$projectId/attribution",
        icon: PieChart,
        shortcut: "G C",
      },
    ],
  },
  { heading: "Improve", items: IMPROVE_ITEMS },
  {
    heading: "Configure",
    items: [
      { label: "Gateway", to: "/$orgId/$projectId/gateway", icon: Waypoints, shortcut: "G G" },
      { label: "Settings", to: "/$orgId/$projectId/settings", icon: Settings, shortcut: "G ," },
    ],
  },
];

/** Every page in rail order, groups flattened: the command palette and the shortcuts read this. */
export const NAV_ITEMS: NavItem[] = NAV_GROUPS.flatMap((group) => group.items);
