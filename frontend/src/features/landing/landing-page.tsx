import { DiagnoseSection } from "./diagnose-section";
import { FinalCtaSection } from "./final-cta-section";
import { HeroSection } from "./hero-section";
import { ImproveSection } from "./improve-section";
import { LandingFooter } from "./landing-footer";
import { LandingNav } from "./landing-nav";
import { LandingScope } from "./landing-scope";
import { OwnSection } from "./own-section";
import { PriceSection } from "./price-section";
import { RouteSection } from "./route-section";
import { SeeSection } from "./see-section";
import { StartSection } from "./start-section";
import { useStartDemo } from "./use-start-demo";

/**
 * Public marketing page at "/" for signed-out visitors (Figma "Landing v8 — Live"). Signed-in
 * users never see it: the route sends them to their workspace first.
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
    <LandingScope className="landing-page relative min-h-dvh overflow-x-clip bg-mono-black">
      <LandingNav {...demoActions} sticky />
      <main>
        <HeroSection {...demoActions} />
        <SeeSection />
        <PriceSection />
        <DiagnoseSection />
        <RouteSection />
        <ImproveSection />
        <StartSection />
        <OwnSection />
        <FinalCtaSection {...demoActions} />
      </main>
      <LandingFooter {...demoActions} />
    </LandingScope>
  );
}
