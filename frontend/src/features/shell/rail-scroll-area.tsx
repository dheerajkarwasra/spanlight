import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";

import { cn } from "@/lib/utils";

import { revealScrollTop, scrollCues, type ScrollCues } from "./rail-scroll";

/** Height of the fade cues, and how far the active page is kept from them. */
const CUE_PX = 32;

interface RailScrollAreaProps {
  children: ReactNode;
  /** Changes on navigation (the pathname): the current page is then scrolled into view. */
  revealKey?: string;
  className?: string;
}

/**
 * The rail's navigation, scrolling on its own between the pinned header and footer (T7-09). Fades
 * show which edge has more pages, and the current page is scrolled into view on every navigation,
 * so a short window never hides where you are. The cues are decorative: links stay reachable by
 * keyboard, and the browser scrolls a focused link into view.
 */
export function RailScrollArea({ children, revealKey, className }: RailScrollAreaProps) {
  const scrollerRef = useRef<HTMLDivElement>(null);
  const [cues, setCues] = useState<ScrollCues>({ above: false, below: false });

  const update = useCallback(() => {
    const scroller = scrollerRef.current;
    if (!scroller) {
      return;
    }
    const next = scrollCues(scroller);
    setCues((current) =>
      current.above === next.above && current.below === next.below ? current : next,
    );
  }, []);

  // Keep the cues right when the window or the list changes size.
  useEffect(() => {
    const scroller = scrollerRef.current;
    if (!scroller) {
      return;
    }
    update();
    const observer = new ResizeObserver(update);
    observer.observe(scroller);
    if (scroller.firstElementChild) {
      observer.observe(scroller.firstElementChild);
    }
    return () => {
      observer.disconnect();
    };
  }, [update]);

  // Bring the current page into view, clear of the fades, before the frame paints.
  useLayoutEffect(() => {
    const scroller = scrollerRef.current;
    const active = scroller?.querySelector<HTMLElement>('[aria-current="page"]');
    if (!scroller || !active) {
      return;
    }
    const box = scroller.getBoundingClientRect();
    const rect = active.getBoundingClientRect();
    const top = rect.top - box.top + scroller.scrollTop;
    scroller.scrollTop = revealScrollTop(scroller, { top, bottom: top + rect.height }, CUE_PX);
    update();
  }, [revealKey, update]);

  return (
    <div className={cn("relative flex min-h-0 flex-col", className)}>
      <div
        ref={scrollerRef}
        data-rail-scroller
        onScroll={update}
        // The padding keeps the links' 2 px offset focus rings inside the clipping box; the scroll
        // padding keeps a link reached by keyboard clear of the 32 px fades.
        className="-mx-1 min-h-0 flex-1 scroll-py-8 [scrollbar-width:none] overflow-y-auto overscroll-contain px-1 py-1 [&::-webkit-scrollbar]:hidden"
      >
        {children}
      </div>
      <ScrollCue side="above" visible={cues.above} />
      <ScrollCue side="below" visible={cues.below} />
    </div>
  );
}

/** A fade into the rail plus a hairline on the edge that has more pages. */
function ScrollCue({ side, visible }: { side: "above" | "below"; visible: boolean }) {
  return (
    <div
      aria-hidden="true"
      data-scroll-cue={side}
      data-visible={visible}
      className={cn(
        "pointer-events-none absolute inset-x-0 h-8 from-rail to-transparent transition-opacity duration-200 motion-reduce:transition-none",
        side === "above"
          ? "top-0 border-t border-rail-tile bg-linear-to-b"
          : "bottom-0 border-b border-rail-tile bg-linear-to-t",
        visible ? "opacity-100" : "opacity-0",
      )}
    />
  );
}
