import { cn } from "@/lib/utils";

type LogoTone = "on-light" | "on-dark";
type LogoSize = "md" | "lg";

interface LogoProps {
  /** `on-light` for the canvas and panels, `on-dark` for the rail and the dark landing page. */
  tone?: LogoTone;
  /** `md` is the 32px mark; `lg` is the 40px mobile app-bar mark. */
  size?: LogoSize;
  withWordmark?: boolean;
  className?: string;
}

const MARK_SIZE: Record<LogoSize, string> = { md: "size-8", lg: "size-10" };
/** Line height after the size: tailwind-merge drops a leading-* that precedes a text-* class. */
const WORDMARK_SIZE: Record<LogoSize, string> = {
  md: "text-[21px] leading-none",
  lg: "text-[26px] leading-none",
};

/**
 * The Cascade mark plus the lowercase "spanlight" wordmark (Figma "Logo · v2" › "Final ·
 * Cascade"). One accessible name for the whole lockup; the drawn letters are hidden from
 * assistive tech.
 */
export function Logo({
  tone = "on-light",
  size = "md",
  withWordmark = true,
  className,
}: LogoProps) {
  return (
    <span
      role="img"
      aria-label="Spanlight"
      className={cn("inline-flex items-center", size === "lg" ? "gap-2.5" : "gap-2", className)}
    >
      <LogoMark tone={tone} size={size} />
      {withWordmark ? (
        <span
          aria-hidden
          className={cn(
            "font-brand font-semibold tracking-[-0.045em] whitespace-nowrap",
            WORDMARK_SIZE[size],
            tone === "on-dark" ? "text-rail-foreground" : "text-foreground",
          )}
        >
          spanlight
        </span>
      ) : null}
    </span>
  );
}

/**
 * Four spans of a trace waterfall on a 64-unit grid: bars 8u tall, 4u apart, each starting 9u
 * further right. The third is the lit one.
 */
const CASCADE_SPANS = [
  { x: 4, y: 10, width: 30, lit: false },
  { x: 13, y: 22, width: 30, lit: false },
  { x: 22, y: 34, width: 38, lit: true },
  { x: 31, y: 46, width: 22, lit: false },
] as const;

/**
 * The Cascade mark. Only the lit span is drawn at full strength: the call the light lands on.
 * On dark surfaces it is lime and the others are white at 30%; on light surfaces everything is
 * the text colour, the unlit spans at 20%. Decorative: pair it with visible text or a label.
 */
export function LogoMark({
  tone = "on-light",
  size = "md",
  className,
}: {
  tone?: LogoTone;
  size?: LogoSize;
  className?: string;
}) {
  const dark = tone === "on-dark";

  return (
    <svg
      aria-hidden
      viewBox="0 0 64 64"
      fill="none"
      className={cn(
        "shrink-0",
        MARK_SIZE[size],
        dark ? "text-rail-foreground" : "text-foreground",
        className,
      )}
    >
      {CASCADE_SPANS.map((span) => (
        <rect
          key={span.y}
          x={span.x}
          y={span.y}
          width={span.width}
          height="8"
          rx="4"
          fill={span.lit && dark ? "var(--lime)" : "currentColor"}
          fillOpacity={span.lit ? 1 : dark ? 0.3 : 0.2}
        />
      ))}
    </svg>
  );
}
