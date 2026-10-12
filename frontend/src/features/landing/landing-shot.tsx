import { useTheme } from "@/lib/theme";
import { cn } from "@/lib/utils";

import { SHOTS, shotSources, type ShotId } from "./landing-shots";

/** Every captured screenshot, keyed by file name ("hero-light@1x.webp" → hashed asset URL). */
const SHOT_URLS: Record<string, string> = Object.fromEntries(
  Object.entries(
    import.meta.glob<string>("../../assets/landing/*.webp", {
      eager: true,
      query: "?url",
      import: "default",
    }),
  ).map(([path, url]) => [path.slice(path.lastIndexOf("/") + 1), url]),
);

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
  const { resolvedTheme } = useTheme();
  const spec = SHOTS[shot];
  const sources = shotSources(shot, resolvedTheme, SHOT_URLS);
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
