import { Menu, Search } from "lucide-react";

import { LogoMark } from "@/components/logo";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { Kbd } from "@/components/ui/kbd";
import { cn } from "@/lib/utils";

import { EnvironmentFilter } from "./environment-filter";
import { ProjectSwitcher } from "./project-switcher";
import type { TimeRangeOptions } from "./time-range-options";
import { TimeRangePicker } from "./time-range-picker";

interface TopbarProps {
  onOpenCommandPalette: () => void;
  /** The page's time range and environment controls; null hides them (2FA-locked org). */
  dataFilters: TimeRangeOptions | null;
}

/** In-panel top bar for desktop (Figma "App/Topbar"): search pill left, controls right. */
export function Topbar({ onOpenCommandPalette, dataFilters }: TopbarProps) {
  return (
    <div className="flex h-11 items-center justify-between gap-4">
      <SearchPill onClick={onOpenCommandPalette} />
      <div className="flex items-center gap-2">
        {dataFilters ? (
          <>
            <TimeRangePicker options={dataFilters} />
            <EnvironmentFilter disabledReason={dataFilters.environmentDisabled} />
          </>
        ) : null}
        <ThemeToggle />
      </div>
    </div>
  );
}

/** Opens the command palette (⌘K / Ctrl+K). */
function SearchPill({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-keyshortcuts="Meta+K Control+K"
      title="Search (⌘K) · Keyboard shortcuts (?)"
      className={cn(
        "flex h-10 w-80 min-w-0 items-center gap-2.5 rounded-full border border-border bg-surface pr-2 pl-4 text-left text-sm text-subtle-foreground shadow-card",
        "transition-colors duration-200 hover:text-muted-foreground xl:w-96",
      )}
    >
      <Search className="size-4 shrink-0" aria-hidden />
      <span className="min-w-0 flex-1 truncate">Search pages, projects, trace IDs…</span>
      <Kbd aria-hidden>⌘K</Kbd>
    </button>
  );
}

interface MobileAppBarProps {
  onOpenCommandPalette: () => void;
  onOpenNavigation: () => void;
  className?: string;
}

/** Phone and tablet header (Figma "App bar"): logo, project switcher, search and menu. */
export function MobileAppBar({
  onOpenCommandPalette,
  onOpenNavigation,
  className,
}: MobileAppBarProps) {
  return (
    <header
      data-app-bar
      className={cn(
        "sticky top-0 z-30 flex items-center justify-between gap-3 bg-background/80 px-4 pt-3.5 pb-2.5 backdrop-blur-md",
        className,
      )}
    >
      <div className="flex min-w-0 items-center gap-2.5">
        <LogoMark size="lg" />
        <ProjectSwitcher variant="appbar" />
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {/* size-11: 44 px touch targets on phones. */}
        <Button size="icon" className="size-11" aria-label="Search" onClick={onOpenCommandPalette}>
          <Search aria-hidden />
        </Button>
        <Button
          variant="primary"
          size="icon"
          className="size-11"
          aria-label="Open navigation"
          onClick={onOpenNavigation}
        >
          <Menu aria-hidden />
        </Button>
      </div>
    </header>
  );
}

/** Mobile placement of the data filters: a wrapping row above the page content. */
export function MobileDataFilters({ options }: { options: TimeRangeOptions }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <TimeRangePicker options={options} />
      <EnvironmentFilter disabledReason={options.environmentDisabled} />
    </div>
  );
}
