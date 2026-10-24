import { formatDate } from "@/lib/format";

const timeFormat = new Intl.DateTimeFormat("en-US", {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

/** "Oct 8, 2026 at 14:32:07" in the viewer's time zone, or null for a missing or bad value. */
export function formatArrival(iso: string | null | undefined): string | null {
  const date = formatDate(iso);
  if (!date || !iso) {
    return null;
  }
  return `${date} at ${timeFormat.format(new Date(iso))}`;
}

/**
 * The trace list window that contains only the project's first trace. `first_trace_at` is the
 * earliest trace start, so `from` is that exact instant (kept as the API's string to preserve
 * microseconds) and `to` is one millisecond later.
 */
export function firstTraceWindow(firstTraceAt: string): { from: string; to: string } | null {
  const start = Date.parse(firstTraceAt);
  if (Number.isNaN(start)) {
    return null;
  }
  return { from: firstTraceAt, to: new Date(start + 1).toISOString() };
}

/**
 * What the Connect step does when it sees the first trace. It opens the trace only when the
 * trace arrived during this visit (the step saw "no traces yet" first) and no key secret created
 * on this visit is on screen: leaving would lose the secret, which is shown once. Otherwise it
 * offers the link.
 */
export function firstTraceAction({
  secretOnScreen,
  arrivedThisVisit,
}: {
  secretOnScreen: boolean;
  arrivedThisVisit: boolean;
}): "open" | "offer" {
  return arrivedThisVisit && !secretOnScreen ? "open" : "offer";
}
