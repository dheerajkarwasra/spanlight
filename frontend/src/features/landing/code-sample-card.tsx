import { Tabs as TabsPrimitive } from "radix-ui";
import { useId, useState } from "react";

import { CopyButton } from "@/components/copy-button";
import { useMediaQuery } from "@/lib/use-media-query";
import { cn } from "@/lib/utils";

import {
  CODE_SAMPLE_IDS,
  CODE_SAMPLES,
  codeToText,
  INSTALL_COMMAND,
  isCodeSampleId,
  type CodeLine,
  type CodeSampleId,
} from "./code-samples";
import { inViewRef } from "./motion";

/** The copy buttons on the black card: mono text that turns white, then reads "Copied". */
const COPY_CLASS =
  "lp-control h-auto min-h-11 rounded-sm px-0 font-mono text-xs font-normal text-mono-400 hover:bg-transparent hover:text-mono-white";

/**
 * The three ways to send a first trace (Figma "Options (tabs)" and "Code card"): the options are
 * the tab list, the black card shows the selected snippet. While the card is on screen the next
 * option is selected every six seconds, shown by the active option's rule filling; hovering or
 * focusing the block holds it, and paused or reduced motion turns it off. All three snippets
 * share one cell, so the card keeps the height of the tallest one.
 */
export function CodeSampleCard() {
  const [selected, setSelected] = useState<CodeSampleId>("python");
  const sample = CODE_SAMPLES[selected];
  const idPrefix = useId();
  // The tab list is a column from the `xl` breakpoint up; arrow keys follow it.
  const desktop = useMediaQuery("(min-width: 1280px)");

  function advance() {
    const next = CODE_SAMPLE_IDS[(CODE_SAMPLE_IDS.indexOf(selected) + 1) % CODE_SAMPLE_IDS.length];
    if (next) {
      setSelected(next);
    }
  }

  return (
    <div ref={inViewRef} className="lp-tabs w-full">
      <TabsPrimitive.Root
        value={selected}
        orientation={desktop ? "vertical" : "horizontal"}
        onValueChange={(value) => {
          if (isCodeSampleId(value)) {
            setSelected(value);
          }
        }}
        className="flex flex-col gap-7 xl:flex-row xl:items-start xl:gap-[72px]"
      >
        <TabsPrimitive.List
          aria-label="Integration"
          className="flex border-b border-mono-line-strong xl:w-[440px] xl:shrink-0 xl:flex-col xl:border-b-0"
        >
          {CODE_SAMPLE_IDS.map((id) => (
            <TabsPrimitive.Trigger
              key={id}
              value={id}
              aria-labelledby={`${idPrefix}-${id}-title`}
              aria-describedby={`${idPrefix}-${id}-summary`}
              className="group lp-control relative flex min-h-11 flex-1 flex-col items-center justify-center px-1 text-mono-500 hover:text-mono-900 data-[state=active]:text-mono-900 xl:flex-none xl:items-start xl:gap-2 xl:border-t xl:border-mono-line-strong xl:px-0 xl:py-6 xl:text-left"
            >
              {/* Underline on phones, top rule on desktop; it fills as the six seconds pass. */}
              <span
                aria-hidden
                onAnimationEnd={advance}
                className="lp-tabfill absolute inset-x-0 -bottom-px hidden h-0.5 bg-mono-900 group-data-[state=active]:block xl:-top-px xl:bottom-auto xl:h-px"
              />
              <span
                id={`${idPrefix}-${id}-title`}
                className="text-sm leading-[1.3] font-medium whitespace-nowrap xl:text-[22px] xl:tracking-[-0.02em]"
              >
                {CODE_SAMPLES[id].label}
              </span>
              <span
                id={`${idPrefix}-${id}-summary`}
                className="hidden text-[15px] leading-[1.55] group-data-[state=active]:text-mono-600 xl:block"
              >
                {CODE_SAMPLES[id].summary}
              </span>
            </TabsPrimitive.Trigger>
          ))}
        </TabsPrimitive.List>

        {/* All summaries share one grid cell, so the tallest sets the height and tabs never jump. */}
        <div className="grid xl:hidden">
          {CODE_SAMPLE_IDS.map((id) => (
            <p
              key={id}
              aria-hidden={id !== selected}
              className={cn(
                "col-start-1 row-start-1 text-[15px] leading-[1.55] text-mono-600",
                id !== selected && "invisible",
              )}
            >
              {CODE_SAMPLES[id].summary}
            </p>
          ))}
        </div>

        <div className="lp-dark flex min-w-0 flex-col overflow-hidden rounded-[14px] bg-mono-black xl:flex-1">
          <div className="flex items-center gap-6 border-b border-mono-800 px-5 font-mono text-xs leading-[1.4] xl:px-6">
            {/* A pointer shortcut to the same tabs; keyboard and screen readers use the list. */}
            {CODE_SAMPLE_IDS.map((id) => (
              <button
                key={id}
                type="button"
                aria-hidden
                tabIndex={-1}
                onClick={() => setSelected(id)}
                className={cn(
                  "lp-control hidden py-4 hover:text-mono-white xl:block",
                  id === selected ? "font-medium text-mono-white" : "text-mono-400",
                )}
              >
                {id}
              </button>
            ))}
            <CopyButton
              value={codeToText(sample.lines)}
              label="Copy"
              aria-label={`Copy the ${sample.label} snippet`}
              showLabel
              showIcon={false}
              variant="ghost"
              className={cn(COPY_CLASS, "ml-auto")}
            />
          </div>

          <div className="grid grid-cols-1">
            {CODE_SAMPLE_IDS.map((id) => (
              <TabsPrimitive.Content
                key={id}
                value={id}
                forceMount
                // The scrollable code inside is the tab stop; the panel itself needn't be one.
                tabIndex={-1}
                className="col-start-1 row-start-1 min-w-0 transition-[opacity,visibility] duration-[180ms] ease-out outline-none data-[state=inactive]:invisible data-[state=inactive]:opacity-0"
              >
                <CodeLines lines={CODE_SAMPLES[id].lines} label={CODE_SAMPLES[id].description} />
              </TabsPrimitive.Content>
            ))}
          </div>

          {/* Only the Python sample needs an install; the row keeps its place for the others. */}
          <div
            className={cn(
              "flex items-center justify-between gap-4 border-t border-mono-800 px-5 font-mono leading-[1.4] xl:px-6",
              selected !== "python" && "invisible",
            )}
          >
            <p className="flex min-w-0 gap-3 overflow-x-auto text-[13px] whitespace-nowrap">
              <span aria-hidden className="text-mono-400">
                $
              </span>
              <code className="text-mono-white">{INSTALL_COMMAND}</code>
            </p>
            <CopyButton
              value={INSTALL_COMMAND}
              label="Copy"
              aria-label="Copy the install command"
              showLabel
              showIcon={false}
              variant="ghost"
              className={cn(COPY_CLASS, "shrink-0")}
            />
          </div>
        </div>
      </TabsPrimitive.Root>
    </div>
  );
}

function CodeLines({ lines, label }: { lines: readonly CodeLine[]; label: string }) {
  return (
    // Focusable so keyboard users can scroll long lines sideways.
    <pre
      tabIndex={0}
      aria-label={label}
      className="overflow-x-auto px-5 py-5 font-mono text-xs leading-[1.75] text-mono-400 xl:px-7 xl:py-[26px] xl:text-[13.5px]"
    >
      <code>
        {lines.map((line, lineIndex) => (
          <span key={lineIndex} className="block min-h-[1lh]">
            {line.map((segment, segmentIndex) => (
              <span
                key={segmentIndex}
                className={segment.tone === "accent" ? "text-mono-white" : undefined}
              >
                {segment.text}
              </span>
            ))}
          </span>
        ))}
      </code>
    </pre>
  );
}
