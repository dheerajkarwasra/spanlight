import { FeatureTile } from "./feature-tile";
import { BillingIllustration } from "./illustrations/billing-illustration";
import { DoctorIllustration } from "./illustrations/doctor-illustration";
import { UsageStatesIllustration } from "./illustrations/usage-states-illustration";
import { SECTION_IDS } from "./links";
import { SectionHeading, TitleAccent } from "./section-heading";

/** The primary benefits: auditable cost and Doctor findings, then provider reconciliation. */
export function CostSection() {
  return (
    <section
      id={SECTION_IDS.cost}
      aria-labelledby="cost-title"
      className="scroll-mt-6 px-5 pt-16 md:px-8 lg:pt-24"
    >
      <SectionHeading
        id="cost-title"
        overline="Why Spanlight"
        title={
          <>
            Understand the cost. <TitleAccent>Find the cause.</TitleAccent>
          </>
        }
        description="Inspect every charge, investigate failures, and compare traced spend with the provider’s bill."
      />
      <div className="mx-auto mt-9 grid max-w-[1200px] grid-cols-1 gap-4 md:grid-cols-2 lg:mt-10 xl:grid-cols-12 xl:gap-5">
        <FeatureTile
          tone="ink"
          overline="Cost breakdown"
          title="Know what every call costs"
          description="Inspect token quantities, rates and tiers line by line. Missing usage or pricing stays unknown, never silently $0."
          illustration={<UsageStatesIllustration />}
          className="min-h-[514px] xl:col-span-7 xl:min-h-[507px]"
        />
        <FeatureTile
          tone="ink"
          overline="Doctor"
          title="Find the cause with the Doctor"
          description="Investigate failures with evidence, a suggested fix and a way to check it worked. Separate provider issues from your own bugs."
          illustration={<DoctorIllustration />}
          className="min-h-[514px] xl:col-span-5 xl:min-h-[507px]"
        />
        <FeatureTile
          layout="split"
          overline="Reconciliation"
          title="Checked against the bill"
          description="Compare traced spend with provider bills from admin APIs or CSV. Group costs by customer, feature or release to find where spend comes from."
          illustration={<BillingIllustration />}
          className="min-h-[466px] md:col-span-2 xl:col-span-12 xl:min-h-[301px]"
        />
      </div>
    </section>
  );
}
