import { Section, SectionHeading } from "./landing-parts";
import { SECTION_IDS } from "./links";
import { ProductClip } from "./product-clip";

/** "See": the product clip with its chapters (Figma "See"). */
export function SeeSection() {
  return (
    <Section id={SECTION_IDS.see} tone="white">
      <SectionHeading
        id={SECTION_IDS.see}
        label="See"
        title="Watch a call land."
        description="Prompt, completion, latency, tokens and cost, seconds after the call runs."
        mobileDescription="Each call appears seconds after it runs, with its prompt, completion, latency, time to first token, tokens and cost, grouped into traces and user sessions."
      />
      <ProductClip />
    </Section>
  );
}
