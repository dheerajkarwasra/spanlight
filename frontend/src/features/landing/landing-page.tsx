import { BeyondTracingSection } from "./beyond-tracing-section";
import { FeaturesSection } from "./features-section";
import { FinalCtaSection } from "./final-cta-section";
import { HeroSection } from "./hero-section";
import { HowItWorksSection } from "./how-it-works-section";
import { LandingFooter } from "./landing-footer";
import { LandingNav } from "./landing-nav";
import { MeshBlob } from "./mesh-blob";
import { OpenSourceSection } from "./open-source-section";
import { useStartDemo } from "./use-start-demo";
import { WorksWithSection } from "./works-with-section";

/**
 * Public marketing page at "/" for signed-out visitors (Figma page "Landing"). Signed-in users
 * never see it: the route sends them to their workspace first.
 */
export function LandingPage() {
  const demo = useStartDemo();
  const demoActions = {
    onTryDemo: () => {
      demo.mutate();
    },
    demoPending: demo.isPending,
  };

  return (
    <div className="relative min-h-dvh overflow-x-clip bg-background">
      <HeroGlow />
      <LandingNav {...demoActions} />
      <main>
        <HeroSection {...demoActions} />
        <WorksWithSection />
        <FeaturesSection />
        <BeyondTracingSection />
        <HowItWorksSection />
        <OpenSourceSection />
        <FinalCtaSection {...demoActions} />
      </main>
      <LandingFooter {...demoActions} />
    </div>
  );
}

/** The pastel mesh behind the nav and headline. */
function HeroGlow() {
  return (
    <>
      <MeshBlob
        tone="lavender"
        className="top-[-120px] left-[-140px] h-[320px] w-[380px] blur-[50px] lg:top-[-200px] lg:left-[calc(50%-900px)] lg:h-[560px] lg:w-[820px] lg:blur-[75px]"
      />
      <MeshBlob
        tone="butter"
        className="top-[-150px] left-[90px] h-[260px] w-[300px] blur-[50px] lg:top-[-260px] lg:left-[calc(50%-260px)] lg:h-[440px] lg:w-[640px] lg:blur-[75px]"
      />
      <MeshBlob
        tone="sky"
        className="top-[-40px] left-[190px] h-[300px] w-[340px] blur-[50px] lg:top-[-140px] lg:left-[calc(50%+140px)] lg:h-[560px] lg:w-[760px] lg:blur-[75px]"
      />
      <MeshBlob
        tone="lavender"
        className="hidden lg:top-[260px] lg:left-[calc(50%+320px)] lg:block lg:h-[420px] lg:w-[520px] lg:blur-[75px]"
      />
    </>
  );
}
