import { ChevronRight, EyeOff, Lightbulb } from "lucide-react";
import { useState, type ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * Figma "D5 Reasoning card": a model's thinking, collapsed above the answer so the answer stays
 * the first thing read. `children` is the (clamped) reasoning text.
 */
export function ReasoningCard({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="flex flex-col rounded-input border border-border bg-surface text-foreground">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => {
          setOpen((current) => !current);
        }}
        className="flex h-8 w-full items-center gap-2 rounded-input px-3 text-left text-label font-semibold hover:bg-surface-hover focus-visible:outline-[1.5px] focus-visible:outline-offset-2 focus-visible:outline-ring"
      >
        <Lightbulb aria-hidden className="size-3.5 shrink-0 text-muted-foreground" />
        Reasoning
        <ChevronRight
          aria-hidden
          className={cn(
            "ml-auto size-4 shrink-0 text-muted-foreground transition-transform",
            open && "rotate-90",
          )}
        />
      </button>
      {open ? (
        <div className="px-3 pb-2.5 text-xs leading-5 text-muted-foreground">{children}</div>
      ) : null}
    </div>
  );
}

/** A static note, dashed so it does not read as a button. */
export function RedactedReasoningNote() {
  return (
    <p className="flex items-center gap-2 rounded-input border border-dashed border-border px-3 py-1.5 text-xs text-muted-foreground">
      <EyeOff aria-hidden className="size-3.5 shrink-0" />
      Reasoning redacted by the provider
    </p>
  );
}
