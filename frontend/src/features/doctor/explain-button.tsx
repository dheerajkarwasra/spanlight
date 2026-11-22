import { Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

import type { ExplainControl } from "./explain-state";

interface ExplainButtonProps {
  control: ExplainControl;
  /** The id of the visible text that explains a locked button. */
  describedBy: string | undefined;
  onClick: () => void;
}

/**
 * "Explain with AI". While busy it stays full ink with a spinner and `aria-busy`; when
 * locked it is `aria-disabled` but still focusable, so the reason beside it can be reached by
 * keyboard. A locked button does nothing on click.
 */
export function ExplainButton({ control, describedBy, onClick }: ExplainButtonProps) {
  const { mode, label, reason, primary } = control;
  const button = (
    <Button
      variant={primary ? "primary" : "secondary"}
      aria-busy={mode === "busy" || undefined}
      aria-disabled={mode === "ready" ? undefined : true}
      aria-describedby={describedBy}
      className={cn(mode === "locked" && "cursor-not-allowed opacity-50 active:translate-y-0")}
      onClick={() => {
        if (mode === "ready") {
          onClick();
        }
      }}
    >
      {mode === "busy" ? <Loader2 className="animate-spin" aria-hidden /> : null}
      {label}
    </Button>
  );
  return reason === null ? button : <Tooltip content={reason}>{button}</Tooltip>;
}
