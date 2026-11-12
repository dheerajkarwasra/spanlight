import { Link } from "@tanstack/react-router";

import { BUTTON_PRIMARY, BUTTON_SECONDARY } from "./button-styles";
import { LightField, type DemoActions, type LightBand } from "./landing-parts";
import { inViewRef, stagger } from "./motion";

/** The upper band of Figma "Close › Light field"; the lower one sits behind the footer wordmark. */
const CTA_BANDS: readonly LightBand[] = [
  {
    left: 600,
    top: 60,
    width: 900,
    height: 140,
    opacity: 0.06,
    rotate: 0,
    blur: 45,
    travel: -180,
    seconds: 11,
  },
];

/** Closing call to action above the footer (Figma "Close › CTA"). */
export function FinalCtaSection({ onTryDemo, demoPending }: DemoActions) {
  return (
    <section
      ref={inViewRef}
      aria-labelledby="final-cta-title"
      className="lp-dark relative overflow-clip border-t border-mono-800 bg-mono-black px-5 pt-16 pb-10 xl:px-6 xl:pt-[120px] xl:pb-24"
    >
      <LightField bands={CTA_BANDS} />
      <div className="lp-view relative mx-auto flex max-w-[640px] flex-col gap-10 xl:max-w-[1200px] xl:flex-row xl:items-end xl:justify-between">
        <h2
          id="final-cta-title"
          className="lp-rise text-[44px] leading-[0.96] font-medium tracking-[-0.05em] text-mono-white xl:text-[104px] xl:whitespace-nowrap"
        >
          <span className="xl:block">Put a light on</span>{" "}
          <span className="xl:block">every LLM call.</span>
        </h2>
        <div style={stagger(1)} className="lp-rise flex flex-col gap-5 xl:items-end">
          <p className="hidden w-[340px] text-right text-[17px] leading-[1.5] text-mono-400 xl:block">
            Explore a sample workspace, or run Spanlight on your own infrastructure.
          </p>
          <div className="flex flex-col gap-2.5 xl:flex-row xl:gap-3">
            <Link to="/signup" className={BUTTON_PRIMARY}>
              Start free
            </Link>
            <button
              type="button"
              onClick={onTryDemo}
              disabled={demoPending}
              aria-busy={demoPending || undefined}
              className={BUTTON_SECONDARY}
            >
              Try the live demo
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}
