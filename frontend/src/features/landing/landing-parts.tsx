import type { CSSProperties, ReactNode } from "react";

import { cn } from "@/lib/utils";

import { delay, inViewRef, stagger } from "./motion";

/** Props shared by everything that offers "Try the live demo". */
export interface DemoActions {
  onTryDemo: () => void;
  demoPending: boolean;
}

type SectionTone = "white" | "paper" | "black";

const SECTION_TONES: Record<SectionTone, string> = {
  white: "bg-mono-white",
  paper: "bg-mono-paper",
  black: "lp-dark bg-mono-black",
};

interface SectionProps {
  id: string;
  tone: SectionTone;
  children: ReactNode;
}

/** A full-width landing section: 1200px column on desktop, a single 640px column below 1280px. */
export function Section({ id, tone, children }: SectionProps) {
  return (
    <section
      id={id}
      aria-labelledby={`${id}-title`}
      className={cn("px-5 py-16 xl:px-6 xl:py-32", SECTION_TONES[tone])}
    >
      <div className="mx-auto flex max-w-[640px] flex-col gap-7 xl:max-w-[1200px] xl:items-center xl:gap-16">
        {children}
      </div>
    </section>
  );
}

interface SectionHeadingProps {
  /** The section id; the heading gets `<id>-title`. */
  id: string;
  /** Mono label beside the square, e.g. "Price". */
  label: string;
  title: ReactNode;
  description: string;
  /** Phone copy when the mobile frame words it differently; `null` when it has none. */
  mobileDescription?: string | null;
  dark?: boolean;
}

/** Square label, display title and one sentence (Figma "Heading"), centred on desktop. */
export function SectionHeading({
  id,
  label,
  title,
  description,
  mobileDescription = description,
  dark = false,
}: SectionHeadingProps) {
  const descriptionClass = cn(
    "lp-rise max-w-[560px] text-base leading-[1.5] xl:text-lg",
    dark ? "text-mono-400" : "text-mono-600",
  );
  return (
    <div
      ref={inViewRef}
      className="lp-view flex flex-col items-start gap-7 xl:items-center xl:gap-5 xl:text-center"
    >
      <p
        className={cn(
          "lp-fade flex items-center gap-2 font-mono text-xs leading-[1.4]",
          dark ? "text-mono-400" : "text-mono-500",
        )}
      >
        <span aria-hidden className={cn("size-[7px]", dark ? "bg-mono-white" : "bg-mono-900")} />
        {label}
      </p>
      <h2
        id={`${id}-title`}
        style={stagger(1)}
        className={cn(
          "lp-rise text-[40px] leading-[1.05] font-medium tracking-[-0.045em] xl:text-[68px] xl:leading-none",
          dark ? "text-mono-white" : "text-mono-900",
        )}
      >
        {title}
      </h2>
      {mobileDescription === description ? (
        <p style={stagger(2)} className={descriptionClass}>
          {description}
        </p>
      ) : (
        <>
          {mobileDescription !== null && (
            <p style={stagger(2)} className={cn(descriptionClass, "xl:hidden")}>
              {mobileDescription}
            </p>
          )}
          <p style={stagger(2)} className={cn(descriptionClass, "hidden xl:block")}>
            {description}
          </p>
        </>
      )}
    </div>
  );
}

/** The grey second line of a two-line section title. */
export function TitleSecondLine({ first, second }: { first: string; second: string }) {
  return (
    <>
      <span className="xl:block">{first}</span>{" "}
      <span className="text-mono-500 xl:block">{second}</span>
    </>
  );
}

interface FigureProps {
  /** The text alternative for the whole drawing. */
  label: string;
  /** False for the hero, whose build-up is timed from page load instead of following the scroll. */
  scrollLinked?: boolean;
  className?: string;
  children: ReactNode;
}

/**
 * A drawn figure: one image with one text alternative; its inner text is hidden from assistive
 * technology. It is also the block its build-up and loops are tied to.
 */
export function Figure({ label, scrollLinked = true, className, children }: FigureProps) {
  return (
    <div
      ref={inViewRef}
      role="img"
      aria-label={label}
      className={cn(scrollLinked && "lp-view", className)}
    >
      <div aria-hidden className="contents">
        {children}
      </div>
    </div>
  );
}

interface WiresProps {
  /** Path data in the coordinate space of `width` × `height`. */
  paths: readonly string[];
  width: number;
  height: number;
  /** Colour of the resting wire. */
  stroke: string;
  /** Colour of the light pulse that runs along it. */
  pulse: string;
  /** Each wire pulses in its own turn instead of all together. */
  turns?: boolean;
  /** Milliseconds before the first pulse, for the timed hero build-up. */
  pulseDelay?: number;
  /** Milliseconds before the first wire draws in (timed hero build-up); staggered otherwise. */
  drawDelay?: number;
  className?: string;
  style?: CSSProperties;
}

const PULSE_SECONDS = 2.4;

/**
 * Wires between the nodes of a live figure: each path is drawn in once, then a light pulse (a
 * second path with an animated dash) keeps running along it while the figure is on screen.
 */
export function Wires({
  paths,
  width,
  height,
  stroke,
  pulse,
  turns = false,
  pulseDelay = 0,
  drawDelay,
  className,
  style,
}: WiresProps) {
  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
      fill="none"
      className={cn("pointer-events-none absolute", className)}
      style={style}
    >
      {paths.map((d, index) => (
        <path
          key={d}
          d={d}
          pathLength={1}
          stroke={stroke}
          strokeWidth={1.5}
          strokeLinecap="round"
          className="lp-draw"
          style={drawDelay === undefined ? stagger(index) : delay(drawDelay + index * 90)}
        />
      ))}
      {paths.map((d, index) => (
        <path
          key={d}
          d={d}
          pathLength={1}
          stroke={pulse}
          strokeWidth={2}
          strokeLinecap="round"
          className={cn("lp-loop lp-pulse", turns && "lp-pulse-turn")}
          style={
            {
              ...delay(pulseDelay + index * (turns ? PULSE_SECONDS * 1000 : 350)),
              "--lp-dur": `${turns ? PULSE_SECONDS * paths.length : PULSE_SECONDS}s`,
            } as CSSProperties
          }
        />
      ))}
    </svg>
  );
}

export interface LightBand {
  left: number;
  /** Distance from the top, or from the bottom when `fromBottom` is set. */
  top: number;
  fromBottom?: boolean;
  width: number;
  height: number;
  /** White at this opacity (0.05 to 0.15). */
  opacity: number;
  rotate: number;
  blur: number;
  /** Sideways travel in px and the seconds one pass takes. */
  travel: number;
  seconds: number;
}

/**
 * The moving background of the black sections: a few soft white bands that drift sideways. Each
 * band is blurred once and then only translated, so it never repaints.
 */
export function LightField({ bands }: { bands: readonly LightBand[] }) {
  return (
    <div aria-hidden className="pointer-events-none absolute inset-0 overflow-clip">
      {bands.map((band) => (
        <div
          key={`${band.left}:${band.top}`}
          className="absolute"
          style={{
            left: band.left,
            [band.fromBottom ? "bottom" : "top"]: band.top,
            rotate: `${band.rotate}deg`,
          }}
        >
          <div
            className="lp-loop lp-drift rounded-full bg-mono-white"
            style={
              {
                width: band.width,
                height: band.height,
                opacity: band.opacity,
                filter: `blur(${band.blur}px)`,
                "--lp-dx": `${band.travel}px`,
                "--lp-dur": `${band.seconds}s`,
              } as CSSProperties
            }
          />
        </div>
      ))}
    </div>
  );
}
