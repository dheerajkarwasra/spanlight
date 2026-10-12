import { createLink } from "@tanstack/react-router";
import { ArrowLeft } from "lucide-react";
import type { ComponentProps } from "react";

import { cn } from "@/lib/utils";

/** The anchor behind `BackLink`: an arrow, then the label, truncated on narrow screens. */
function BackAnchor({ className, children, ref, ...props }: ComponentProps<"a">) {
  return (
    <a
      ref={ref}
      {...props}
      className={cn(
        "inline-flex min-h-6 w-fit max-w-full items-center gap-1.5 rounded-sm text-[13px] font-semibold text-muted-foreground transition-colors hover:text-foreground",
        className,
      )}
    >
      <ArrowLeft aria-hidden className="size-3.5 shrink-0" />
      <span className="truncate">{children}</span>
    </a>
  );
}

/**
 * "← Prompts": the link above a page title back to where it came from. A typed router link, so
 * it takes `to`, `params` and `search` like `Link`; the label is its children.
 */
export const BackLink = createLink(BackAnchor);
