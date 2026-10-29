import { Tabs as TabsPrimitive } from "radix-ui";
import { useState } from "react";

import { CopyButton } from "@/components/copy-button";
import { useMediaQuery } from "@/lib/use-media-query";
import { cn } from "@/lib/utils";

import {
  CODE_SAMPLE_IDS,
  CODE_SAMPLES,
  codeToText,
  isCodeSampleId,
  visibleLines,
  type CodeLine,
  type CodeSampleId,
  type CodeTone,
} from "./code-samples";

/** Phones get the compact variants of the landing illustrations (Figma "Landing — Mobile"). */
const COMPACT_QUERY = "(max-width: 767px)";

const TONE_CLASSES: Record<CodeTone, string | undefined> = {
  plain: undefined,
  muted: "text-rail-muted-foreground",
  accent: "text-lime",
};

/**
 * The dark code card next to the steps (Figma "Code card"): Python / OpenAI / OTLP tabs, a
 * lime copy button, numbered lines and the "trace received" result. Phones get the shorter
 * snippets from the mobile design, and copy exactly what is shown.
 */
export function CodeSampleCard({ className }: { className?: string }) {
  const [selected, setSelected] = useState<CodeSampleId>("python");
  const compact = useMediaQuery(COMPACT_QUERY);
  const sample = CODE_SAMPLES[selected];
  const copyText = codeToText(visibleLines(sample, compact));

  return (
    <TabsPrimitive.Root
      value={selected}
      onValueChange={(value) => {
        if (isCodeSampleId(value)) {
          setSelected(value);
        }
      }}
      className={cn(
        "flex min-w-0 flex-col gap-4 rounded-card bg-hero-card px-[18px] pt-4 pb-[18px] text-hero-card-foreground md:gap-[22px] md:px-7 md:pt-6 md:pb-7 dark:border dark:border-border",
        className,
      )}
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <TabsPrimitive.List
          aria-label="Integration"
          className="flex gap-0.5 rounded-full bg-rail-tile p-[3px] md:p-1"
        >
          {CODE_SAMPLE_IDS.map((id) => (
            <TabsPrimitive.Trigger
              key={id}
              value={id}
              className={cn(
                "rounded-full px-2 py-[5px] text-xs font-semibold text-rail-muted-foreground transition-colors md:px-3.5 md:py-1.5 md:text-label",
                "hover:text-rail-foreground focus-visible:outline-lime",
                "data-[state=active]:bg-rail-foreground data-[state=active]:text-rail",
              )}
            >
              {CODE_SAMPLES[id].label}
            </TabsPrimitive.Trigger>
          ))}
        </TabsPrimitive.List>
        <CopyButton
          value={copyText}
          label="Copy"
          aria-label={`Copy the ${sample.label} snippet`}
          showLabel
          variant="highlight"
          size="sm"
          className="ml-auto h-auto gap-[5px] py-1.5 pr-3 pl-2.5 text-xs md:gap-1.5 md:py-[7px] md:pr-3.5 md:pl-3 md:text-label [&_svg]:size-[13px] [&_svg]:text-lime-foreground md:[&_svg]:size-3.5"
        />
      </div>

      {CODE_SAMPLE_IDS.map((id) => (
        <TabsPrimitive.Content
          key={id}
          value={id}
          // The scrollable code inside is the tab stop; the panel itself needn't be one.
          tabIndex={-1}
          className="min-w-0 outline-none"
        >
          <CodeLines
            lines={visibleLines(CODE_SAMPLES[id], compact)}
            label={CODE_SAMPLES[id].description}
          />
        </TabsPrimitive.Content>
      ))}

      <p className="flex items-center gap-2 rounded-input bg-rail-tile px-3 py-[9px] font-mono text-2xs leading-[1.5] text-rail-foreground md:gap-2.5 md:px-4 md:py-2.5 md:text-xs">
        <span
          aria-hidden
          className="flex size-4 shrink-0 items-center justify-center rounded-full bg-lime md:size-[18px]"
        >
          <span className="size-[5px] rounded-full bg-lime-foreground md:size-1.5" />
        </span>
        <span className="min-w-0 truncate">
          Trace <span className="hidden md:inline">answer_ticket </span>received ·{" "}
          <span className="hidden md:inline">2 spans · </span>1.42 s · $0.0031
        </span>
      </p>
    </TabsPrimitive.Root>
  );
}

function CodeLines({ lines, label }: { lines: readonly CodeLine[]; label: string }) {
  return (
    <div className="flex gap-3.5 font-mono text-xs leading-[1.5] md:gap-5 md:text-code md:leading-[1.7]">
      <ol
        aria-hidden
        className="shrink-0 text-right text-rail-subtle-foreground select-none md:min-w-[22px]"
      >
        {lines.map((_, index) => (
          <li key={index}>{index + 1}</li>
        ))}
      </ol>
      {/* Focusable so keyboard users can scroll long lines horizontally. */}
      <pre
        tabIndex={0}
        aria-label={label}
        className="min-w-0 flex-1 overflow-x-auto text-rail-foreground focus-visible:outline-offset-2 focus-visible:outline-lime"
      >
        <code>
          {lines.map((line, lineIndex) => (
            <span key={lineIndex} className="block min-h-[1lh]">
              {line.map((segment, segmentIndex) => (
                <span key={segmentIndex} className={TONE_CLASSES[segment.tone]}>
                  {segment.text}
                </span>
              ))}
            </span>
          ))}
        </code>
      </pre>
    </div>
  );
}
