import { createContext, useContext, type CSSProperties } from "react";

export interface LandingMotion {
  paused: boolean;
  toggle: () => void;
}

/** Provided by `LandingScope`; read through `useLandingMotion`. */
export const MotionContext = createContext<LandingMotion | null>(null);

/** Whether landing motion is paused, and the toggle behind the footer's "Pause motion" button. */
export function useLandingMotion(): LandingMotion {
  const motion = useContext(MotionContext);
  if (!motion) {
    throw new Error("useLandingMotion must be used inside a LandingScope");
  }
  return motion;
}

/**
 * Ref callback for a block with motion: marks it `data-seen` the first time it enters the
 * viewport (one-shot build-ups without scroll timelines) and keeps `data-onscreen` current
 * (loops run only on screen). Attributes are written straight to the DOM, so nothing re-renders.
 */
export function inViewRef(element: Element | null) {
  if (!element) {
    return;
  }
  if (typeof IntersectionObserver === "undefined") {
    element.setAttribute("data-seen", "");
    element.setAttribute("data-onscreen", "true");
    return;
  }
  const observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) {
          element.setAttribute("data-seen", "");
        }
        element.setAttribute("data-onscreen", String(entry.isIntersecting));
      }
    },
    { threshold: 0.1 },
  );
  observer.observe(element);
  return () => {
    observer.disconnect();
  };
}

/** Place in a stagger (`--i`), read by the build-up classes in landing.css. */
export function stagger(index: number): CSSProperties {
  return { "--i": index } as CSSProperties;
}

/** Explicit delay in milliseconds (`--d`) for the timed hero build-up and for loops. */
export function delay(ms: number): CSSProperties {
  return { "--d": `${ms}ms` } as CSSProperties;
}
