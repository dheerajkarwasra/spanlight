import { Link, useLocation } from "@tanstack/react-router";
import { ArrowUpRight, BookOpen, ChevronDown } from "lucide-react";
import { useState } from "react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { docsLink } from "@/lib/docs-links";

import { OpenCriticalBadge } from "./nav-badge";
import { activeTab, type Hub, type HubTab } from "./nav-items";
import { useProjectParams } from "./project-context";

const PILL_CLASSES =
  "group flex h-9 items-center justify-center gap-2 rounded-full px-4 text-sm font-medium whitespace-nowrap text-muted-foreground transition-colors hover:bg-surface-hover hover:text-foreground data-[status=active]:bg-ink data-[status=active]:font-semibold data-[status=active]:text-ink-foreground";

const DOT_CLASSES =
  "hidden size-1.5 shrink-0 rounded-full bg-lime group-data-[status=active]:block dark:bg-lime-foreground";

/**
 * The hub's pages as a level-2 row above the page: a card-shaped pill row from 768 px, and below
 * that a pill naming the current page that opens the list in a bottom sheet. The active pill is ink
 * with a lime dot. The hub's docs page sits at the right of the row.
 */
export function HubTabs({ hub }: { hub: Hub }) {
  const pathname = useLocation({ select: (location) => location.pathname });
  const current = activeTab(hub, pathname);

  return (
    <div className="mb-6 flex items-center justify-between gap-4">
      <SectionPicker hub={hub} current={current} />
      <nav aria-label={`${hub.label} sections`} className="hidden md:block">
        <ul className="flex w-fit items-center gap-0.5 rounded-full border border-border bg-surface p-1 shadow-card">
          {hub.tabs.map((tab) => (
            <HubTabLink key={tab.to} tab={tab} active={tab === current} />
          ))}
        </ul>
      </nav>
      <a
        href={docsLink(hub.docs)}
        target="_blank"
        rel="noreferrer"
        className="hidden min-h-6 shrink-0 items-center gap-1.5 text-label font-semibold text-muted-foreground transition-colors hover:text-foreground md:inline-flex"
      >
        <BookOpen aria-hidden className="size-3.5" />
        Docs
        <ArrowUpRight aria-hidden className="size-3.5" />
        <span className="sr-only">(opens in a new tab)</span>
      </a>
    </div>
  );
}

function HubTabLink({
  tab,
  active,
  onNavigate,
}: {
  tab: HubTab;
  active: boolean;
  onNavigate?: () => void;
}) {
  const params = useProjectParams();
  return (
    <li>
      {/* Active state comes from `activeTab`; exact matching keeps the router from marking a second tab. */}
      <Link
        to={tab.to}
        params={params}
        activeOptions={{ exact: true }}
        activeProps={{}}
        onClick={onNavigate}
        className={PILL_CLASSES}
        data-status={active ? "active" : undefined}
        aria-current={active ? "page" : undefined}
      >
        {tab.label}
        {tab.badge === "open-critical" ? <OpenCriticalBadge /> : null}
        <span aria-hidden className={DOT_CLASSES} />
      </Link>
    </li>
  );
}

function SectionPicker({ hub, current }: { hub: Hub; current: HubTab | null }) {
  const [open, setOpen] = useState(false);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <button
          type="button"
          className="flex h-11 w-full items-center gap-2 rounded-full border border-input bg-surface pr-3.5 pl-4 text-left md:hidden"
        >
          <span className="text-label font-medium text-muted-foreground">Section</span>
          <span className="min-w-0 flex-1 truncate text-sm font-semibold text-foreground">
            {current?.label ?? hub.label}
          </span>
          <ChevronDown aria-hidden className="size-4 shrink-0 text-foreground" />
        </button>
      </DialogTrigger>
      <DialogContent className="top-auto bottom-2 max-h-[calc(100dvh-1rem)] w-[calc(100%-1rem)] max-w-none translate-y-0 overflow-y-auto rounded-card p-5">
        <div className="flex flex-col gap-1.5 pr-8">
          <DialogTitle>{hub.label}</DialogTitle>
          <DialogDescription>Choose a section.</DialogDescription>
        </div>
        <nav aria-label={`${hub.label} sections`}>
          <ul className="flex flex-col gap-0.5">
            {hub.tabs.map((tab) => (
              <HubTabLink
                key={tab.to}
                tab={tab}
                active={tab === current}
                onNavigate={() => {
                  setOpen(false);
                }}
              />
            ))}
          </ul>
        </nav>
      </DialogContent>
    </Dialog>
  );
}
