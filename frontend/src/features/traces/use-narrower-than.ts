import { useLayoutEffect, useState, type RefObject } from "react";

/** True while the element is narrower than `px`: lets code follow a container-query layout. */
export function useNarrowerThan(ref: RefObject<HTMLElement | null>, px: number): boolean {
  const [narrow, setNarrow] = useState(false);
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) {
      return;
    }
    const measure = () => {
      setNarrow(element.clientWidth < px);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => {
      observer.disconnect();
    };
  }, [ref, px]);
  return narrow;
}
