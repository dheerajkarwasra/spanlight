import type { ReactNode } from "react";

import { Kbd } from "@/components/ui/kbd";
import { cn } from "@/lib/utils";

/** Figma "Key chip": the bordered key hint next to work-mode actions and options. */
export function KeyChip({ children, className }: { children: string; className?: string }) {
  return (
    <Kbd
      className={cn(
        "h-6 min-w-6 rounded-tooltip border border-border-strong px-[7px] text-xs text-foreground",
        className,
      )}
    >
      {children}
    </Kbd>
  );
}

/** A work-mode action with its key hint beside it (an empty slot when it has no key). */
export function ActionRow({ keyHint, children }: { keyHint: string | null; children: ReactNode }) {
  return (
    <div className="flex items-center gap-2.5">
      {children}
      {keyHint === null ? (
        <span aria-hidden className="hidden size-6 sm:block" />
      ) : (
        <KeyChip className="hidden sm:inline-flex">{keyHint}</KeyChip>
      )}
    </div>
  );
}
