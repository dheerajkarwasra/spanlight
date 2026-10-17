import { useEffect, useRef } from "react";

import { SegmentedControl } from "@/components/ui/segmented-control";
import type { InsightStatus } from "@/lib/api";
import { revealInScroller } from "@/lib/reveal-in-scroller";

import { STATUS_LABELS, STATUSES } from "./insight-format";

/** A status tab of the insights list, or the Precision tab. */
export type DoctorTab = InsightStatus | "precision";

interface StatusTabsProps {
  value: DoctorTab;
  onChange: (tab: DoctorTab) => void;
}

const OPTIONS: { value: DoctorTab; label: string }[] = [
  ...STATUSES.map((status) => ({ value: status, label: STATUS_LABELS[status] })),
  { value: "precision", label: "Precision" },
];

/**
 * Figma "Doctor/Status tabs": Open, Acknowledged, Muted and Resolved, then Precision, as one
 * choice. The row scrolls sideways on a phone so the last tab is always reachable, and the
 * selected tab is scrolled into view, on a change of tab and when the row gets narrower.
 */
export function StatusTabs({ value, onChange }: StatusTabsProps) {
  const scrollerRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const scroller = scrollerRef.current;
    if (!scroller) {
      return;
    }
    const reveal = () => {
      const active = scroller.querySelector<HTMLElement>('[data-state="checked"]');
      if (active) {
        revealInScroller(scroller, active);
      }
    };
    reveal();
    // A narrower row after a resize or a rotation must not hide the selected tab either.
    if (typeof ResizeObserver === "undefined") {
      return;
    }
    const observer = new ResizeObserver(reveal);
    observer.observe(scroller);
    return () => {
      observer.disconnect();
    };
  }, [value]);
  return (
    <div
      ref={scrollerRef}
      className="-mx-4 [scrollbar-width:none] overflow-x-auto px-4 py-1 sm:mx-0 sm:px-0"
    >
      <SegmentedControl
        aria-label="Doctor view"
        value={value}
        onValueChange={onChange}
        options={OPTIONS}
      />
    </div>
  );
}
