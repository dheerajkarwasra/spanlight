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

/**
 * A work-mode action with its key hint inside the button's right edge, so every action is the
 * same width with or without a key. The hint is hidden below `sm`, where there is no keyboard.
 */
export function ActionRow({ keyHint, children }: { keyHint: string | null; children: ReactNode }) {
  return (
    <div className="relative flex [&:has(:disabled)>kbd]:opacity-50">
      {children}
      {keyHint === null ? null : (
        <KeyChip className="pointer-events-none absolute top-1/2 right-2 hidden -translate-y-1/2 sm:inline-flex">
          {keyHint}
        </KeyChip>
      )}
    </div>
  );
}
