import { BeyondTracingSection } from "./beyond-tracing-section";
import { CostSection } from "./cost-section";
import { FinalCtaSection } from "./final-cta-section";
import { HeroSection } from "./hero-section";
import { HowItWorksSection } from "./how-it-works-section";
import { LandingFooter } from "./landing-footer";
import { LandingNav } from "./landing-nav";
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
      <LandingNav {...demoActions} />
      <main>
        <HeroSection {...demoActions} />
        <WorksWithSection />
        <HowItWorksSection />
        <CostSection />
        <BeyondTracingSection />
        <OpenSourceSection />
        <FinalCtaSection {...demoActions} />
      </main>
      <LandingFooter {...demoActions} />
    </div>
  );
}
