import { useLayoutEffect, type RefObject } from "react";

/** Clear space (px) kept between the sticky summary bar and a scrolled-to element. */
const GAP = 8;

/**
 * While a trace view is open, keep focused and scrolled-to elements below its sticky bars (WCAG
 * 2.4.11): the bar's sticky offset (the phone app bar above it below `lg`), its height (taller
 * when its Doctor findings wrap) and 8 px, set on `box`, the element that scrolls the view (the
 * page's root, or the peek sheet's scroll box once it has mounted).
 */
export function usePageScrollPadding(bar: RefObject<HTMLElement | null>, box: HTMLElement | null) {
  useLayoutEffect(() => {
    const element = bar.current;
    if (!element || !box) {
      return;
    }
    function measure() {
      if (element && box) {
        const top = Number.parseFloat(getComputedStyle(element).top) || 0;
        box.style.setProperty("scroll-padding-top", `${top + element.offsetHeight + GAP}px`);
      }
    }
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => {
      observer.disconnect();
      box.style.removeProperty("scroll-padding-top");
    };
  }, [bar, box]);
}
