import { ArrowUpRight, BookOpen } from "lucide-react";
import type { ReactNode } from "react";

import { DialogHeading } from "@/components/dialog-heading";
import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent } from "@/components/ui/dialog";
import { Kbd } from "@/components/ui/kbd";
import { docsLink } from "@/lib/docs-links";

import { HUBS, type Hub } from "./nav-items";

interface ShortcutHelpProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/** The bordered key hint, styled like work mode's key chip. */
function KeyChip({ children }: { children: string }) {
  return (
    <Kbd className="h-6 min-w-6 rounded-tooltip border border-border-strong px-[7px] text-xs text-foreground">
      {children}
    </Kbd>
  );
}

function Row({ keys, children }: { keys: string[]; children: ReactNode }) {
  return (
    <li className="flex items-center gap-3 rounded-input bg-surface-muted px-3.5 py-2.5">
      <span className="flex w-[110px] shrink-0 items-center gap-1">
        {keys.map((key) => (
          <KeyChip key={key}>{key}</KeyChip>
        ))}
      </span>
      <span className="min-w-0 flex-1 text-sm font-medium text-foreground">{children}</span>
    </li>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-1.5">
      <h3 className="text-overline text-muted-foreground uppercase">{title}</h3>
      <ul className="flex flex-col gap-1.5">{children}</ul>
    </section>
  );
}

/** One "Go to" row per hub: the first tab's keys as chips, the hub's other tabs inline after it. */
function HubRow({ hub }: { hub: Hub }) {
  const [first, ...rest] = hub.tabs.filter((tab) => tab.shortcut !== undefined);
  if (first?.shortcut === undefined) {
    return null;
  }
  return (
    <Row keys={first.shortcut.split(" ")}>
      {/* A hub's own overview tab reads as the hub: G G is "Gateway", not "Overview". */}
      {first.label === "Overview" ? hub.label : first.label}
      {rest.map((tab) => (
        <span key={tab.to}>
          <span className="text-muted-foreground"> · {tab.shortcut} </span>
          {tab.label}
        </span>
      ))}
    </Row>
  );
}

/**
 * Figma "Keyboard help overlay (?)": the app-wide keymap, opened with `?` or from the user menu.
 * `?` or `Esc` closes it. Review work mode has its own overlay.
 */
export function ShortcutHelp({ open, onOpenChange }: ShortcutHelpProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        hideClose
        className="max-h-[calc(100dvh-2rem)] max-w-[520px] gap-[18px] overflow-y-auto rounded-card p-6 sm:p-7"
        onKeyDown={(event) => {
          if (event.key === "?") {
            event.preventDefault();
            onOpenChange(false);
          }
        }}
      >
        <DialogHeading
          title="Keyboard shortcuts"
          description="Move around without the mouse. Press ? or Esc to close."
        />
        <Section title="Global">
          <Row keys={["⌘", "K"]}>
            Open the command palette
            <span className="text-muted-foreground"> · Ctrl K</span>
          </Row>
          <Row keys={["?"]}>Show this help</Row>
        </Section>
        <Section title="Go to (G, then a key)">
          {HUBS.map((hub) => (
            <HubRow key={hub.id} hub={hub} />
          ))}
        </Section>
        <Section title="Trace waterfall">
          <Row keys={["↑", "↓"]}>Move between spans</Row>
          <Row keys={["←", "→"]}>Collapse or expand</Row>
          <Row keys={["Home", "End"]}>First or last span</Row>
        </Section>
        <Section title="Trace peek">
          <Row keys={["J", "K"]}>Next or previous trace in the peek</Row>
        </Section>
        <div className="flex items-center justify-between gap-4">
          <a
            href={docsLink()}
            target="_blank"
            rel="noreferrer"
            className="inline-flex min-h-6 items-center gap-2 py-1 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground"
          >
            <BookOpen aria-hidden className="size-4" />
            Read the docs
            <ArrowUpRight aria-hidden className="size-3.5" />
            <span className="sr-only">(opens in a new tab)</span>
          </a>
          <DialogClose asChild>
            <Button variant="primary">Close</Button>
          </DialogClose>
        </div>
      </DialogContent>
    </Dialog>
  );
}
