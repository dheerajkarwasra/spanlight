import { Card } from "@/components/ui/card";

interface TradeOffCardProps {
  /** The sentence from `formatTradeOff`. */
  sentence: string;
  /** "Run #3 against Run #2 · 50 items in both runs · 48 scored on both sides". */
  detail: string;
}

/** Figma "Experiments — Compare" Trade-off: the ink hero card with the one-line verdict. */
export function TradeOffCard({ sentence, detail }: TradeOffCardProps) {
  return (
    <Card variant="hero" className="flex flex-col gap-3 p-6 sm:p-7">
      <div className="flex items-center justify-between gap-3">
        <p className="text-[11px] font-semibold tracking-[0.08em] text-lime uppercase">Trade-off</p>
        <span className="rounded-full bg-lime px-3 py-1 text-xs font-bold text-lime-foreground">
          Higher is better
        </span>
      </div>
      <p
        aria-live="polite"
        className="text-[22px] leading-tight font-extrabold tracking-[-0.03em] sm:text-[28px]"
      >
        {sentence}
      </p>
      <p className="text-sm text-rail-muted-foreground">{detail}</p>
    </Card>
  );
}
