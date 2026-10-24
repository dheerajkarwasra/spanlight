import { useEffect, type RefObject } from "react";

/**
 * Keeps two scroll boxes at the same vertical ratio. The move it makes fires a scroll event on
 * the other box; that echo is skipped so the two never chase each other. Below `md` the compare
 * columns stack without a height cap, so they never scroll and this stays inert.
 */
export function useSyncedScroll(
  a: RefObject<HTMLElement | null>,
  b: RefObject<HTMLElement | null>,
): void {
  useEffect(() => {
    const first = a.current;
    const second = b.current;
    if (!first || !second) {
      return;
    }
    let echo: HTMLElement | null = null;
    const follow = (source: HTMLElement, target: HTMLElement) => () => {
      if (echo === source) {
        echo = null;
        return;
      }
      const range = source.scrollHeight - source.clientHeight;
      if (range <= 0) {
        return;
      }
      const next = (source.scrollTop / range) * (target.scrollHeight - target.clientHeight);
      // A move under a pixel fires no event, so it must not leave an echo to skip.
      if (Math.abs(target.scrollTop - next) < 1) {
        return;
      }
      echo = target;
      target.scrollTop = next;
    };
    const onFirst = follow(first, second);
    const onSecond = follow(second, first);
    first.addEventListener("scroll", onFirst, { passive: true });
    second.addEventListener("scroll", onSecond, { passive: true });
    return () => {
      first.removeEventListener("scroll", onFirst);
      second.removeEventListener("scroll", onSecond);
    };
  }, [a, b]);
}
