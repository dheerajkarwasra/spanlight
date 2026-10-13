import { cn } from "@/lib/utils";

import { SHOTS, type ShotId } from "./landing-shots";
import { useShotSources } from "./use-shot-sources";

interface LandingShotProps {
  shot: ShotId;
  /** The hero image loads eagerly with high priority; everything else is lazy. */
  priority?: boolean;
  className?: string;
}

/**
 * One real product screenshot, in the site's current theme. Width and height are fixed so the
 * page does not shift while it loads. Until the screenshot has been captured the frame stays
 * empty (and is hidden from assistive tech) instead of showing a stand-in image.
 */
export function LandingShot({ shot, priority = false, className }: LandingShotProps) {
  const spec = SHOTS[shot];
  const sources = useShotSources(shot);
  const frameClass = cn("block h-auto w-full bg-surface-muted", className);

  if (!sources) {
    return (
      <span
        aria-hidden
        className={frameClass}
        style={{ aspectRatio: `${spec.width} / ${spec.height}` }}
      />
    );
  }

  return (
    <img
      src={sources.src}
      srcSet={sources.srcSet}
      sizes={spec.sizes}
      width={spec.width}
      height={spec.height}
      alt={spec.alt}
      loading={priority ? "eager" : "lazy"}
      decoding={priority ? "sync" : "async"}
      fetchPriority={priority ? "high" : "auto"}
      className={cn(frameClass, "object-cover object-left-top")}
    />
  );
}
