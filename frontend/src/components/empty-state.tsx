import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

interface EmptyStateProps {
  icon: LucideIcon;
  title: string;
  description: ReactNode;
  /** Usually one primary pill button, optionally a secondary one. */
  action?: ReactNode;
  className?: string;
}

/** A quiet icon tile, a bold title, a muted explanation and the next step. */
export function EmptyState({ icon: Icon, title, description, action, className }: EmptyStateProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-4 px-6 py-12 text-center",
        className,
      )}
    >
      <span
        aria-hidden
        className="flex size-12 items-center justify-center rounded-tile border border-border bg-surface-muted"
      >
        <Icon className="size-5 text-muted-foreground" strokeWidth={1.75} />
      </span>
      <div className="flex max-w-sm flex-col gap-1.5">
        <h3 className="text-base font-semibold tracking-[-0.01em] text-foreground">{title}</h3>
        <div className="text-sm text-muted-foreground">{description}</div>
      </div>
      {action ? (
        <div className="mt-1 flex flex-wrap items-center justify-center gap-2">{action}</div>
      ) : null}
    </div>
  );
}
