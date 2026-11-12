import { Link } from "@tanstack/react-router";
import type { CSSProperties } from "react";

import { INSTALL_COMMAND } from "./code-samples";
import { HeroDiagram, HeroDiagramMobile } from "./hero-diagram";
import { BUTTON_PRIMARY, BUTTON_SECONDARY } from "./button-styles";
import { Figure, LightField, type DemoActions, type LightBand } from "./landing-parts";
import { delay, inViewRef } from "./motion";

/** Figma "Light field (moving background)": four soft bands behind the hero. */
const HERO_BANDS: readonly LightBand[] = [
  {
    left: -328,
    top: 80,
    width: 1000,
    height: 200,
    opacity: 0.12,
    rotate: 8,
    blur: 55,
    travel: 160,
    seconds: 9,
  },
  {
    left: 600,
    top: 185,
    width: 1100,
    height: 160,
    opacity: 0.09,
    rotate: -6,
    blur: 55,
    travel: -140,
    seconds: 12,
  },
  {
    left: -31,
    top: 620,
    width: 900,
    height: 220,
    opacity: 0.1,
    rotate: 8,
    blur: 55,
    travel: 120,
    seconds: 7,
  },
  {
    left: 500,
    top: 875,
    width: 1000,
    height: 180,
    opacity: 0.08,
    rotate: -6,
    blur: 55,
    travel: -160,
    seconds: 10,
  },
];

const DIAGRAM_ALT =
  "Model calls become one trace; Spanlight shows its cost line by line and the cause of a failure";

/** Headline and pitch rise 24px, the headline first (Figma motion spec "Hero entrance"). */
function rise(ms: number): CSSProperties {
  return { ...delay(ms), "--lp-rise": "24px", "--lp-enter": "600ms" } as CSSProperties;
}

/** Headline, pitch, the sign-up and demo actions, the install command and the live diagram. */
export function HeroSection({ onTryDemo, demoPending }: DemoActions) {
  return (
    <section
      ref={inViewRef}
      aria-labelledby="hero-title"
      className="lp-dark relative overflow-clip bg-mono-black px-5 py-14 xl:px-6 xl:py-24"
    >
      <LightField bands={HERO_BANDS} />
      <div className="relative mx-auto flex max-w-[640px] flex-col gap-10 xl:max-w-[1200px] xl:items-center xl:gap-16">
        <div className="flex flex-col gap-7 xl:items-center xl:text-center">
          <div className="flex flex-col gap-4 xl:items-center">
            <p className="lp-fade font-mono text-xs leading-[1.4] text-mono-400 xl:hidden">
              Open-source LLM observability
            </p>
            <h1
              id="hero-title"
              style={rise(100)}
              className="lp-rise text-[46px] leading-[0.96] font-medium tracking-[-0.05em] text-mono-white xl:text-[96px]"
            >
              <span className="block">Every LLM call,</span>{" "}
              <span className="block text-mono-500">accounted for.</span>
            </h1>
          </div>
          <p
            style={rise(350)}
            className="lp-rise text-[17px] leading-[1.5] text-mono-400 xl:w-[620px] xl:text-[19px]"
          >
            Trace it, price it line by line, check it against the bill and find out why it failed.
            Open source, on one Postgres.
          </p>
          <div
            style={rise(500)}
            className="lp-rise flex flex-col gap-2.5 pt-2 xl:flex-row xl:items-center xl:gap-3"
          >
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
            <p className="flex items-center gap-3 overflow-x-auto rounded-[10px] border border-mono-800 px-4 py-3 font-mono text-[13px] leading-[1.4] whitespace-nowrap xl:rounded-full">
              <span aria-hidden className="text-mono-400">
                $
              </span>
              <code className="text-mono-white select-all">{INSTALL_COMMAND}</code>
            </p>
          </div>
        </div>

        <Figure label={DIAGRAM_ALT} scrollLinked={false} className="w-full xl:w-auto">
          <HeroDiagram />
          <HeroDiagramMobile />
        </Figure>

        <p className="font-mono text-xs leading-[1.4] text-mono-400">
          Worked example: the demo support bot, priced at claude-sonnet-5-5 list rates.
        </p>
      </div>
    </section>
  );
}
