import { Pause, Play } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { useMediaQuery } from "@/lib/use-media-query";
import { cn } from "@/lib/utils";

/**
 * The hero product clip (Figma "Product clip"): a muted screen recording of real demo traces in
 * a browser-window frame. It plays on its own unless the visitor prefers reduced motion; then
 * the poster stays up with a play button. One button pauses and resumes it either way.
 */
export function ProductClip({ className }: { className?: string }) {
  const reduceMotion = useMediaQuery("(prefers-reduced-motion: reduce)");
  const videoRef = useRef<HTMLVideoElement>(null);
  const [playing, setPlaying] = useState(false);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    if (reduceMotion) {
      video.pause();
    } else {
      // A blocked autoplay just leaves the poster and the play button up.
      video.play().catch(() => undefined);
    }
  }, [reduceMotion]);

  function toggle() {
    const video = videoRef.current;
    if (!video) return;
    if (video.paused) {
      video.play().catch(() => undefined);
    } else {
      video.pause();
    }
  }

  return (
    <figure className={cn("mx-auto flex max-w-[1180px] flex-col gap-3 lg:gap-4", className)}>
      <div className="overflow-hidden rounded-card border border-border bg-surface">
        <div
          aria-hidden
          className="flex h-8 items-center gap-3 border-b border-border bg-surface-muted px-3 lg:h-10 lg:gap-4 lg:px-4"
        >
          <span className="flex gap-1.5">
            {[0, 1, 2].map((dot) => (
              <span key={dot} className="size-2 rounded-full bg-border-strong lg:size-2.5" />
            ))}
          </span>
          <span className="rounded-full bg-surface px-3.5 py-1 text-xs font-medium text-muted-foreground">
            spanlight.dev
          </span>
        </div>
        <div className="relative aspect-video bg-surface-muted">
          <video
            ref={videoRef}
            aria-label="A support agent sends a call that appears live on the Overview; the trace is opened by its ID with its spans and line-by-line cost; a six-turn session is read turn by turn; the Doctor shows a repeated tool call with evidence and a suggested fix"
            className="size-full object-cover"
            width={1920}
            height={1080}
            poster="/landing/hero-poster.jpg"
            preload="none"
            muted
            loop
            playsInline
            onPlay={() => setPlaying(true)}
            onPause={() => setPlaying(false)}
          >
            <source src="/landing/hero.webm" type="video/webm" />
            <source src="/landing/hero.mp4" type="video/mp4" />
          </video>
          <Button
            variant="primary"
            size="icon-lg"
            aria-label={playing ? "Pause product clip" : "Play product clip"}
            onClick={toggle}
            className={cn(
              "absolute",
              playing
                ? "right-3 bottom-3 size-10 [&_svg]:size-4"
                : "top-1/2 left-1/2 size-11 -translate-x-1/2 -translate-y-1/2 lg:size-16 lg:[&_svg]:size-6",
            )}
          >
            {playing ? <Pause aria-hidden /> : <Play aria-hidden className="translate-x-px" />}
          </Button>
        </div>
      </div>
      <figcaption className="text-center text-xs font-medium text-muted-foreground lg:text-sm">
        Real traces from a demo support bot. Recorded on Spanlight.
      </figcaption>
    </figure>
  );
}
