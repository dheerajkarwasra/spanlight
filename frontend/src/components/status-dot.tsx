import { CircleX, TriangleAlert } from "lucide-react";

import { cn } from "@/lib/utils";

type StatusDotState = "ok" | "error" | "warning" | "unset" | "live";

interface StatusDotProps {
  /** `ok` success green, `error` rose X-circle, `warning` amber triangle, `unset` grey, `live` lime with a pulse. */
  state: StatusDotState;
  /**
   * Screen-reader text, e.g. "Succeeded". Omit when the dot sits next to visible text that
   * already says the same thing: it is then hidden from assistive technology.
   */
  label?: string;
  /** Pulse ring (disabled under reduced motion). On by default for `live`. */
  pulse?: boolean;
  className?: string;
}

const STATE_CLASSES: Record<"ok" | "unset" | "live", string> = {
  ok: "bg-success text-success",
  unset: "bg-subtle-foreground text-subtle-foreground",
  live: "bg-lime text-lime",
};

const STATE_ICONS = { error: CircleX, warning: TriangleAlert } as const;

/**
 * An 8px status dot (Figma "P5/StatusDot"). `error` and `warning` render a 12px X-circle or
 * triangle instead, so the shape carries the state and colour is never the only signal (WCAG 1.4.1).
 */
export function StatusDot({ state, label, pulse, className }: StatusDotProps) {
  const pulsing = pulse ?? state === "live";
  const a11y = {
    role: label ? "img" : undefined,
    "aria-label": label,
    "aria-hidden": label ? undefined : true,
  } as const;
  if (state === "error" || state === "warning") {
    const Icon = STATE_ICONS[state];
    return (
      <Icon
        {...a11y}
        strokeWidth={2.25}
        className={cn(
          "size-3 shrink-0",
          state === "error" ? "text-danger" : "text-warning",
          pulsing && "animate-live-pulse",
          className,
        )}
      />
    );
  }
  return (
    <span
      {...a11y}
      className={cn(
        "inline-block size-2 shrink-0 rounded-full",
        STATE_CLASSES[state],
        pulsing && "animate-live-pulse",
        className,
      )}
    />
  );
}

export type { StatusDotState };
