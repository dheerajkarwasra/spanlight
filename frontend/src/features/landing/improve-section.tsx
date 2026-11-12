import type { ReactNode } from "react";

import { Figure, Section, SectionHeading } from "./landing-parts";
import { inViewRef, stagger } from "./motion";
import { SECTION_IDS } from "./links";

/*
 * The names "support/answer", "answer-v1-vs-v2" and "support_turn" come from the demo workspace.
 * The interval positions and the review queue states are illustrations and show no values.
 */
const PROMPT_VERSIONS = [
  { version: "v2", state: "current" },
  { version: "v1", state: "previous" },
] as const;

/** Interval and pass-rate dot as percentages of the scale. */
const EVAL_RUNS = [
  { run: "run #1", left: 24.4, width: 48.8, dot: 48 },
  { run: "run #2", left: 48.8, width: 39, dot: 71.5 },
] as const;

const REVIEW_QUEUE = ["reviewed", "next", "queued"] as const;

const FIGURE =
  "flex w-full flex-col gap-3 rounded-[10px] border border-mono-line bg-mono-paper p-5";
const MONO_TITLE = "font-mono text-[13px] leading-[1.4] font-medium text-mono-900";
const ROW = "flex items-center justify-between rounded-md px-3 py-2.5";

function PromptsFigure() {
  return (
    <Figure
      label="The prompt support/answer with two versions: v2 is current, v1 is the previous one."
      className={FIGURE}
    >
      <span className={MONO_TITLE}>support/answer</span>
      {PROMPT_VERSIONS.map((item, index) => {
        const current = index === 0;
        return (
          <span
            key={item.version}
            style={stagger(index)}
            className={`lp-rise ${ROW} [--lp-rise:8px] ${current ? "bg-mono-mist" : ""}`}
          >
            <span className="flex items-center gap-2.5 font-mono text-[13px] leading-[1.4] text-mono-900">
              <span
                className={`size-2 rounded-full ${current ? "bg-mono-900" : "bg-mono-line-strong"}`}
              />
              <span className={current ? "font-medium" : undefined}>{item.version}</span>
            </span>
            <span
              className={`font-mono text-xs leading-[1.4] ${current ? "text-mono-600" : "text-mono-500"}`}
            >
              {item.state}
            </span>
          </span>
        );
      })}
    </Figure>
  );
}

function EvalsFigure() {
  return (
    <Figure
      label="The experiment answer-v1-vs-v2: the pass rate of two runs, each with its 95% confidence interval. Illustration, no values shown."
      className={FIGURE}
    >
      <span className="flex justify-between leading-[1.4]">
        <span className={MONO_TITLE}>answer-v1-vs-v2</span>
        <span className="font-mono text-xs text-mono-500">pass rate · 95% CI</span>
      </span>
      {EVAL_RUNS.map((item, index) => (
        <span key={item.run} className="flex items-center gap-4">
          <span className="w-14 font-mono text-xs leading-[1.4] text-mono-500">{item.run}</span>
          <span className="relative h-4 flex-1">
            <span className="absolute inset-x-0 top-[7.5px] h-px bg-mono-line" />
            {/* The interval grows outwards from its pass-rate dot. */}
            <span
              style={{
                left: `${item.left}%`,
                width: `${item.width}%`,
                transformOrigin: `${((item.dot - item.left) / item.width) * 100}% 50%`,
                ...stagger(index),
              }}
              className="lp-grow absolute top-1.5 h-1 rounded-sm bg-mono-400"
            />
            <span
              style={{ left: `${item.dot}%` }}
              className="absolute top-0.5 size-3 -translate-x-1/2 rounded-full bg-mono-900"
            />
          </span>
        </span>
      ))}
      <span className="text-xs leading-[1.4] text-mono-500">Illustration. No values shown.</span>
    </Figure>
  );
}

function ReviewFigure() {
  return (
    <Figure
      label="The review queue: one support_turn trace is reviewed, one is next and one is queued."
      className={FIGURE}
    >
      <span className={MONO_TITLE}>Review queue</span>
      {REVIEW_QUEUE.map((state, index) => (
        <span
          key={state}
          style={stagger(index)}
          className={`lp-rise ${ROW} [--lp-rise:8px] ${state === "next" ? "bg-mono-mist" : ""}`}
        >
          <span
            className={`flex items-center gap-2.5 font-mono text-[13px] leading-[1.4] ${state === "reviewed" ? "text-mono-500" : "text-mono-900"}`}
          >
            <span
              className={`size-2.5 rounded-xs ${state === "reviewed" ? "bg-mono-900" : "border border-mono-500"}`}
            />
            support_turn
          </span>
          <span
            className={`font-mono text-xs leading-[1.4] ${state === "next" ? "text-mono-600" : "text-mono-500"}`}
          >
            {state}
          </span>
        </span>
      ))}
    </Figure>
  );
}

interface Tile {
  title: string;
  text: string;
  mobileText: string;
  figure: ReactNode;
}

const TILES: readonly Tile[] = [
  {
    title: "Prompts",
    text: "Versions, labels, a playground and exact replay of any recorded call.",
    mobileText:
      "Versioned prompts with labels, a playground to test them side by side, and exact replay of any recorded call.",
    figure: <PromptsFigure />,
  },
  {
    title: "Evals",
    text: "Pass rates with 95% confidence intervals, judges checked against your reviewers.",
    mobileText:
      "Datasets and experiments report pass rates with 95% confidence intervals. LLM judges show their agreement with your reviewers: Cohen’s kappa, F1 and a confusion matrix.",
    figure: <EvalsFigure />,
  },
  {
    title: "Daily review",
    text: "A nightly queue of the traces worth reading.",
    mobileText:
      "A nightly queue picks the traces worth reading, so yesterday’s traffic gets a look every morning.",
    figure: <ReviewFigure />,
  },
];

/** "Improve": prompts, evals and the daily review as three tiles (Figma "Improve"). */
export function ImproveSection() {
  return (
    <Section id={SECTION_IDS.improve} tone="paper">
      <SectionHeading
        id={SECTION_IDS.improve}
        label="Improve"
        title="Then make it better."
        description="Prompts, evals and a daily review, on the same traces."
        mobileDescription={null}
      />
      <ul ref={inViewRef} className="lp-view flex w-full flex-col xl:flex-row xl:gap-6">
        {TILES.map((tile, index) => (
          <li
            key={tile.title}
            style={stagger(index)}
            className={`lp-rise flex flex-col gap-2 border-t py-6 xl:h-[358px] xl:flex-1 xl:justify-between xl:rounded-2xl xl:border xl:border-mono-line xl:bg-mono-white xl:p-7 ${index === 0 ? "border-mono-900" : "border-mono-line-strong"}`}
          >
            <div className="hidden xl:block">{tile.figure}</div>
            <div className="flex flex-col gap-2">
              <h3 className="text-[28px] leading-[1.1] font-medium tracking-[-0.03em] text-mono-900">
                {tile.title}
              </h3>
              <p className="text-[15px] leading-[1.5] text-mono-600 xl:hidden">{tile.mobileText}</p>
              <p className="hidden text-[15px] leading-[1.5] text-mono-600 xl:block">{tile.text}</p>
            </div>
          </li>
        ))}
      </ul>
    </Section>
  );
}
