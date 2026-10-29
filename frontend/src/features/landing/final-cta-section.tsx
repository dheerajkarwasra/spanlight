import { Button } from "@/components/ui/button";

import { DOCS_LINKS } from "./links";

interface FinalCtaSectionProps {
  onTryDemo: () => void;
  demoPending: boolean;
}

/** Closing call to action (Figma "Section/Final CTA"). */
export function FinalCtaSection({ onTryDemo, demoPending }: FinalCtaSectionProps) {
  return (
    <section
      aria-labelledby="final-cta-title"
      className="relative flex flex-col items-center gap-[22px] px-5 py-16 text-center md:px-8 lg:gap-7 lg:py-24"
    >
      <h2
        id="final-cta-title"
        className="relative max-w-[340px] text-[40px] leading-[1.04] font-extrabold tracking-[-0.04em] text-foreground md:max-w-none lg:text-display-hero"
      >
        See your first trace{" "}
        <span className="font-serif-accent text-[48px] leading-none text-accent lg:text-serif-hero">
          in minutes.
        </span>
      </h2>
      <p className="relative max-w-[320px] text-base leading-[1.55] text-muted-foreground md:max-w-[560px] lg:text-[19px]">
        Explore a sample workspace, or follow the guide to run Spanlight on your own infrastructure.
      </p>
      <div className="relative flex w-full flex-col gap-2.5 pt-2 md:w-auto md:flex-row md:gap-3 lg:pt-3">
        <Button variant="primary" size="lg" loading={demoPending} onClick={onTryDemo}>
          Try the live demo
        </Button>
        <Button asChild size="lg" className="shadow-none">
          <a href={DOCS_LINKS.selfHosting}>Self-host Spanlight</a>
        </Button>
      </div>
    </section>
  );
}
