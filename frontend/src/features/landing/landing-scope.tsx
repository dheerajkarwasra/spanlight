import { useContext, useEffect, useMemo, useState, type ReactNode, type Ref } from "react";

import { useMediaQuery } from "@/lib/use-media-query";
import { cn } from "@/lib/utils";

import { MotionContext } from "./motion";

import "./landing.css";

interface LandingScopeProps {
  as?: "div" | "header" | "footer";
  className?: string;
  ref?: Ref<HTMLElement>;
  children: ReactNode;
}

/**
 * The landing colour and motion scope: the `landing` class (the "Landing mono" variables) and
 * `data-motion`, which the "Pause motion" button flips. Motion starts paused for visitors who
 * prefer reduced motion. A scope inside another one (the nav inside the page) shares its state.
 */
export function LandingScope({ as: Tag = "div", className, ref, children }: LandingScopeProps) {
  const parent = useContext(MotionContext);
  const reduceMotion = useMediaQuery("(prefers-reduced-motion: reduce)");
  const [choice, setChoice] = useState<boolean | null>(null);
  const paused = choice ?? reduceMotion;
  const own = useMemo(
    () => ({
      paused,
      toggle: () => {
        setChoice(!paused);
      },
    }),
    [paused],
  );
  const motion = parent ?? own;

  // Loops also stop while the tab is in the background.
  useEffect(() => {
    function sync() {
      document.documentElement.toggleAttribute("data-lp-hidden", document.hidden);
    }
    sync();
    document.addEventListener("visibilitychange", sync);
    return () => {
      document.removeEventListener("visibilitychange", sync);
      document.documentElement.removeAttribute("data-lp-hidden");
    };
  }, []);

  return (
    <MotionContext.Provider value={motion}>
      <Tag
        ref={ref as Ref<HTMLDivElement>}
        className={cn("landing", className)}
        data-motion={motion.paused ? "paused" : "play"}
      >
        {children}
      </Tag>
    </MotionContext.Provider>
  );
}
