import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

import { ResponsiveCopy, type Copy } from "./responsive-copy";

interface FeatureTileProps {
  overline: string;
  title: string;
  description: Copy;
  illustration: ReactNode;
  /** `ink` is a primary benefit tile; `surface` is a supporting feature tile. */
  tone?: "surface" | "ink";
  /** `split` puts copy and illustration side by side on wide screens. */
  layout?: "stacked" | "split";
  /** True when the illustration is a real screenshot with its own alt text, not decoration. */
  illustrationIsContent?: boolean;
  className?: string;
}

/**
 * Bento feature tile (Figma "Landing/Feature tile"): overline, title and description on top,
 * the illustration pinned to the bottom. Drawn illustrations are decorative markup, hidden from
 * assistive tech; the description carries the meaning. Screenshots are content and stay visible.
 */
export function FeatureTile({
  overline,
  title,
  description,
  illustration,
  tone = "surface",
  layout = "stacked",
  illustrationIsContent = false,
  className,
}: FeatureTileProps) {
  const ink = tone === "ink";
  return (
    <article
      className={cn(
        "flex min-w-0 flex-col gap-[22px] overflow-hidden rounded-card px-[22px] py-6 md:gap-6 md:p-7",
        ink
          ? "bg-hero-card text-hero-card-foreground dark:border dark:border-border"
          : "border border-border bg-surface",
        layout === "split" && "xl:flex-row xl:items-center xl:gap-12",
        className,
      )}
    >
      <div
        className={cn("flex flex-col gap-2.5", layout === "split" && "xl:w-[400px] xl:shrink-0")}
      >
        <p className={cn("text-overline uppercase", ink ? "text-lime" : "text-accent")}>
          {overline}
        </p>
        <h3 className={cn("text-h2", ink ? "text-rail-foreground" : "text-foreground")}>{title}</h3>
        <p className={cn("text-sm", ink ? "text-rail-muted-foreground" : "text-muted-foreground")}>
          <ResponsiveCopy copy={description} />
        </p>
      </div>
      <div
        aria-hidden={illustrationIsContent ? undefined : true}
        className={cn(
          "flex min-w-0 flex-1 flex-col justify-end",
          layout === "split" && "xl:justify-center",
        )}
      >
        {illustration}
      </div>
    </article>
  );
}
