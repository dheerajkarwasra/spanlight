import { Link } from "@tanstack/react-router";
import { ArrowLeft, Keyboard } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useProjectParams } from "@/features/shell";
import type { QueueProgress } from "@/lib/api";

import { KeyChip } from "./key-chip";
import { ProgressBar } from "./progress-bar";
import { progressSummary } from "./queue-format";

interface WorkBarProps {
  queueId: string;
  queueName: string | null;
  /** "Item 25 of 60 · claimed by you", "Queue finished", "No item claimed". */
  subtitle: string;
  progress: QueueProgress | null;
  onOpenHelp: () => void;
}

/**
 * The work-mode top bar (Figma "Work bar"): Exit review, the queue name, the progress and the
 * shortcuts button. The progress line is a polite live region, so each finished item is
 * announced.
 */
export function WorkBar({ queueId, queueName, subtitle, progress, onOpenHelp }: WorkBarProps) {
  const { orgId, projectId } = useProjectParams();
  return (
    <header className="flex flex-wrap items-center gap-x-4 gap-y-3">
      <Button variant="secondary" className="shadow-none" asChild>
        <Link to="/$orgId/$projectId/queues/$queueId" params={{ orgId, projectId, queueId }}>
          <ArrowLeft aria-hidden />
          <span className="hidden sm:inline">Exit review</span>
          <span className="sm:hidden">Exit</span>
          <KeyChip className="hidden sm:inline-flex">Esc</KeyChip>
        </Link>
      </Button>
      <div className="flex min-w-0 flex-1 flex-col gap-0.5 lg:flex-none">
        <h1 className="truncate text-lg font-bold text-foreground">{queueName ?? "Review"}</h1>
        <p className="text-xs font-medium text-muted-foreground">{subtitle}</p>
      </div>
      <div className="hidden flex-1 lg:block" />
      <div className="flex w-full flex-col gap-1.5 sm:w-[260px]">
        <p role="status" aria-live="polite" className="text-xs font-semibold text-foreground">
          {progress === null ? "Progress: loading" : `Progress: ${progressSummary(progress)}`}
        </p>
        {progress === null ? null : <ProgressBar progress={progress} />}
      </div>
      <Button
        variant="secondary"
        className="hidden shadow-none sm:inline-flex"
        aria-keyshortcuts="?"
        onClick={onOpenHelp}
      >
        <Keyboard aria-hidden />
        Shortcuts
        <KeyChip>?</KeyChip>
      </Button>
    </header>
  );
}
