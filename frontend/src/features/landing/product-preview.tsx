import { Lock } from "lucide-react";

import { cn } from "@/lib/utils";

import { LandingShot } from "./landing-shot";
import { MeshBlob } from "./mesh-blob";
import { useShotSources } from "./use-shot-sources";

/**
 * The hero's product shot (Figma "Product preview", v3): a real screenshot of the experiment
 * comparison from the live demo, in a browser frame over a pastel glow, with a caption saying
 * where it comes from.
 */
export function ProductPreview({ className }: { className?: string }) {
  // The caption claims a real screen, so it waits for the screenshot (finding T7-08).
  const captured = useShotSources("hero") !== null;
  return (
    <figure className={cn("relative pb-[30px] lg:pb-16", className)}>
      <MeshBlob
        tone="lavender"
        className="top-[60px] left-[-10px] h-[220px] w-[360px] blur-[45px] lg:top-[160px] lg:left-[calc(50%-520px)] lg:h-[460px] lg:w-[1040px] lg:blur-[80px]"
      />
      <MeshBlob
        tone="sky"
        className="top-[160px] left-[150px] h-[160px] w-[240px] blur-[45px] lg:top-[480px] lg:left-[calc(50%+40px)] lg:h-[340px] lg:w-[640px] lg:blur-[75px]"
      />
      <MeshBlob
        tone="butter"
        className="hidden lg:top-[500px] lg:left-[calc(50%-560px)] lg:block lg:h-[320px] lg:w-[560px] lg:blur-[75px]"
      />

      <div className="relative mx-auto flex max-w-[1203px] flex-col gap-1.5 rounded-[20px] border border-surface bg-surface p-1.5 shadow-lg backdrop-blur-[10px] lg:gap-2 lg:rounded-[32px] lg:border-[1.5px] lg:p-2.5 dark:border-border">
        <div aria-hidden className="flex h-5 items-center justify-between px-1.5 lg:h-9 lg:px-2.5">
          <span className="flex w-[60px] gap-1 lg:w-[120px] lg:gap-[7px]">
            <span className="size-[7px] rounded-full bg-border lg:size-[11px]" />
            <span className="size-[7px] rounded-full bg-border lg:size-[11px]" />
            <span className="size-[7px] rounded-full bg-border lg:size-[11px]" />
          </span>
          <span className="flex items-center gap-1 rounded-full bg-background px-2.5 py-[3px] font-mono text-[9px] text-muted-foreground lg:gap-2 lg:border lg:border-border lg:px-4 lg:py-1.5 lg:text-xs">
            <Lock className="size-2 text-subtle-foreground lg:size-3" />
            <span>
              <span className="hidden lg:inline">spanlight demo · </span>experiments / compare
            </span>
          </span>
          <span className="w-[60px] lg:w-[120px]" />
        </div>
        <LandingShot shot="hero" priority className="rounded-[14px] lg:rounded-[22px]" />
      </div>

      {captured ? (
        <figcaption className="relative mx-auto mt-4 max-w-[320px] text-center text-xs font-medium text-muted-foreground md:max-w-none lg:mt-6 lg:text-sm">
          A real screen from the live demo: two prompt versions scored on the same dataset.
        </figcaption>
      ) : null}
    </figure>
  );
}
