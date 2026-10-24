import { useOpenCriticalCount } from "./use-open-critical-count";

/** The Doctor's count of critical insights that still need attention; nothing while there are none. */
export function OpenCriticalBadge() {
  const count = useOpenCriticalCount();
  if (!count) {
    return null;
  }
  return (
    <span
      role="img"
      aria-label={`${count} critical ${count === 1 ? "insight needs" : "insights need"} attention`}
      className="rounded-full bg-danger px-1.5 py-px text-2xs font-bold text-danger-foreground tabular"
    >
      <span aria-hidden>{count > 99 ? "99+" : count}</span>
    </span>
  );
}
