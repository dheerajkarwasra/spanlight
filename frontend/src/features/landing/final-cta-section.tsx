import { Button } from "@/components/ui/button";

import { DOCS_LINKS } from "./links";
import { MeshBlob } from "./mesh-blob";

interface FinalCtaSectionProps {
  onTryDemo: () => void;
  demoPending: boolean;
}

/** Closing call to action on the pastel mesh (Figma "Section/Final CTA"). */
export function FinalCtaSection({ onTryDemo, demoPending }: FinalCtaSectionProps) {
  return (
    <section
      aria-labelledby="final-cta-title"
      className="relative flex flex-col items-center gap-[22px] px-5 py-28 text-center md:px-8 lg:gap-7 lg:py-40"
    >
      <MeshBlob
        tone="lavender"
        className="top-[140px] left-[calc(50%-235px)] h-[220px] w-[260px] blur-[45px] lg:top-[180px] lg:left-[calc(50%-520px)] lg:h-[380px] lg:w-[620px] lg:blur-[75px]"
      />
      <MeshBlob
        tone="butter"
        className="top-[100px] left-[calc(50%-95px)] h-[180px] w-[220px] blur-[45px] lg:top-[120px] lg:left-[calc(50%-200px)] lg:h-[320px] lg:w-[520px] lg:blur-[75px]"
      />
      <MeshBlob
        tone="sky"
        className="top-[200px] left-[calc(50%-15px)] h-[220px] w-[240px] blur-[45px] lg:top-[220px] lg:left-[calc(50%-20px)] lg:h-[380px] lg:w-[620px] lg:blur-[75px]"
      />

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
        Explore the live demo workspace, or self-host the whole stack with one command.
      </p>
      <div className="relative flex w-full flex-col gap-2.5 pt-2 md:w-auto md:flex-row md:gap-3 lg:pt-3">
        <Button variant="primary" size="lg" loading={demoPending} onClick={onTryDemo}>
          Try the live demo
        </Button>
        <Button asChild size="lg">
          <a href={DOCS_LINKS.quickstart}>Read the docs</a>
        </Button>
      </div>
    </section>
  );
}
