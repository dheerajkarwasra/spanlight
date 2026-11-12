import { Pause, Play } from "lucide-react";
import { useContext, useEffect, useRef, useState } from "react";

import { useMediaQuery } from "@/lib/use-media-query";
import { cn } from "@/lib/utils";

import { MotionContext } from "./motion";

/** What the clip shows, in order (Figma "Clip chapters"). The clip is read as four equal parts. */
const CHAPTERS = [
  "A call lands live on the Overview, no reload.",
  "The trace opens by its ID, with its spans and line-by-line cost.",
  "A six-turn session is read turn by turn.",
  "The Doctor shows a repeated tool call, with evidence and a suggested fix.",
] as const;

/**
 * The product clip (Figma "Product clip figure"): a muted screen recording of real demo traces in
 * a browser-window frame, greyscale at rest and in colour while it plays, with a chapter line
 * under it that fills with clip time. It plays while it is on screen unless the visitor prefers
 * reduced motion; then the poster stays up with a play button. One button pauses and resumes it.
 */
export function ProductClip({ className }: { className?: string }) {
  const reduceMotion = useMediaQuery("(prefers-reduced-motion: reduce)");
  const videoRef = useRef<HTMLVideoElement>(null);
  const fillRefs = useRef<(HTMLSpanElement | null)[]>([]);
  // The visitor's own pause outlives scrolling the clip off screen and back.
  const userPausedRef = useRef(false);
  // The visitor pressed play or a chapter, so playback may resume where autoplay is off.
  const userStartedRef = useRef(false);
  const pendingChapterRef = useRef<number | null>(null);
  const motionPaused = useContext(MotionContext)?.paused ?? false;
  const autoplay = !reduceMotion && !motionPaused;
  const [playing, setPlaying] = useState(false);
  const [chapter, setChapter] = useState(0);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    // "Pause motion" and reduced motion stop automatic playback, including one started earlier.
    if (!autoplay) userStartedRef.current = false;
    let onScreen = typeof IntersectionObserver === "undefined";
    function sync() {
      if (!video) return;
      const wanted = (autoplay || userStartedRef.current) && !userPausedRef.current;
      if (onScreen && !document.hidden && wanted) {
        // A blocked autoplay just leaves the poster and the play button up.
        video.play().catch(() => undefined);
      } else {
        video.pause();
      }
    }
    const observer = onScreen
      ? null
      : new IntersectionObserver(
          (entries) => {
            onScreen = entries.some((entry) => entry.isIntersecting);
            sync();
          },
          { threshold: 0.25 },
        );
    observer?.observe(video);
    if (!observer) sync();
    document.addEventListener("visibilitychange", sync);
    return () => {
      observer?.disconnect();
      document.removeEventListener("visibilitychange", sync);
    };
  }, [autoplay]);

  function toggle() {
    const video = videoRef.current;
    if (!video) return;
    userPausedRef.current = !video.paused;
    if (video.paused) {
      userStartedRef.current = true;
      video.play().catch(() => undefined);
    } else {
      video.pause();
    }
  }

  function seekTo(video: HTMLVideoElement, index: number) {
    video.currentTime = (index / CHAPTERS.length) * video.duration;
  }

  function seek(index: number) {
    const video = videoRef.current;
    if (!video) return;
    if (Number.isFinite(video.duration)) {
      seekTo(video, index);
    } else {
      // Nothing is loaded yet (`preload="none"`): seek once the duration is known.
      pendingChapterRef.current = index;
    }
    userPausedRef.current = false;
    userStartedRef.current = true;
    video.play().catch(() => undefined);
  }

  function showProgress(video: HTMLVideoElement) {
    const progress = video.duration ? (video.currentTime / video.duration) * CHAPTERS.length : 0;
    // Written to each line directly: no re-render and no inherited variable per tick.
    fillRefs.current.forEach((fill, index) => {
      if (fill) {
        fill.style.transform = `scaleX(${Math.min(1, Math.max(0, progress - index))})`;
      }
    });
    setChapter(Math.min(CHAPTERS.length - 1, Math.floor(progress)));
  }

  return (
    <figure className={cn("flex w-full flex-col gap-6", className)}>
      <div className="overflow-hidden rounded-[14px] border border-mono-line-strong">
        <div
          aria-hidden
          className="flex h-7 items-center gap-2 bg-mono-black px-3 xl:h-11 xl:px-[18px]"
        >
          {[0, 1, 2].map((dot) => (
            <span key={dot} className="size-1.5 rounded-full bg-mono-600 xl:size-2.5" />
          ))}
          <span className="hidden font-mono text-xs leading-[1.4] text-mono-400 xl:block">
            spanlight.dev
          </span>
        </div>
        <div className="relative aspect-video bg-mono-mist">
          <video
            ref={videoRef}
            aria-label="A support agent sends a call that appears live on the Overview; the trace is opened by its ID with its spans and line-by-line cost; a six-turn session is read turn by turn; the Doctor shows a repeated tool call with evidence and a suggested fix"
            className={cn(
              "size-full object-cover transition-[filter] duration-[400ms] ease-out",
              !playing && "grayscale",
            )}
            width={1920}
            height={1080}
            poster="/landing/hero-poster.jpg"
            preload="none"
            muted
            loop
            playsInline
            onPlay={() => setPlaying(true)}
            onPause={() => setPlaying(false)}
            onLoadedMetadata={(event) => {
              if (pendingChapterRef.current !== null) {
                seekTo(event.currentTarget, pendingChapterRef.current);
                pendingChapterRef.current = null;
              }
            }}
            onTimeUpdate={(event) => showProgress(event.currentTarget)}
          >
            <source src="/landing/hero.webm" type="video/webm" />
            <source src="/landing/hero.mp4" type="video/mp4" />
          </video>
          <button
            type="button"
            aria-label={playing ? "Pause product clip" : "Play product clip"}
            onClick={toggle}
            className="lp-control absolute right-3 bottom-3 flex size-11 items-center justify-center rounded-full bg-mono-black text-mono-white hover:bg-mono-900 xl:right-4 xl:bottom-4"
          >
            {playing ? (
              <Pause aria-hidden className="size-4 fill-current" />
            ) : (
              <Play aria-hidden className="size-4 translate-x-px fill-current" />
            )}
          </button>
        </div>
      </div>

      <ol className="flex flex-col xl:flex-row xl:gap-6 xl:pt-1">
        {CHAPTERS.map((text, index) => (
          <li key={text} className="xl:flex-1">
            <button
              type="button"
              aria-current={index === chapter ? "step" : undefined}
              onClick={() => seek(index)}
              className={cn(
                "lp-control flex min-h-11 w-full flex-col gap-3.5 pb-3 text-left text-sm leading-[1.5] hover:text-mono-900 xl:pb-0 xl:text-[15px]",
                index === chapter ? "font-medium text-mono-900" : "text-mono-500",
              )}
            >
              <span aria-hidden className="relative block h-0.5 w-full bg-mono-line">
                <span
                  ref={(element) => {
                    fillRefs.current[index] = element;
                  }}
                  className="absolute inset-0 origin-left scale-x-0 bg-mono-900"
                />
              </span>
              {text}
            </button>
          </li>
        ))}
      </ol>

      <figcaption className="text-xs leading-[1.4] text-mono-500 xl:text-[13px]">
        Real traces from a demo support bot. Recorded on Spanlight.
      </figcaption>
    </figure>
  );
}
