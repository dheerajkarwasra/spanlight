import { useId, type ReactNode } from "react";

import { TooltipContent, TooltipRoot, TooltipTrigger } from "@/components/ui/tooltip";

interface DisabledFilterProps {
  /** The control's name, e.g. "Time range". */
  label: string;
  /** Why the page doesn't honour the control. */
  reason: string;
  /** The control, rendered disabled. */
  children: ReactNode;
}

/**
 * A header control the page can't honour: the disabled control can't take focus, so this
 * focusable group carries its name and the reason, read on focus and shown in a tooltip on hover
 * and focus.
 */
export function DisabledFilter({ label, reason, children }: DisabledFilterProps) {
  const reasonId = useId();
  return (
    <TooltipRoot>
      <TooltipTrigger asChild>
        <div
          role="group"
          aria-label={label}
          aria-describedby={reasonId}
          tabIndex={0}
          className="rounded-full"
        >
          {children}
          <span id={reasonId} className="sr-only">
            {reason}
          </span>
        </div>
      </TooltipTrigger>
      <TooltipContent>{reason}</TooltipContent>
    </TooltipRoot>
  );
}
