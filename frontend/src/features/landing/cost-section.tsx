import { FeatureTile } from "./feature-tile";
import { AttributionIllustration } from "./illustrations/attribution-illustration";
import { BillingIllustration } from "./illustrations/billing-illustration";
import { UsageStatesIllustration } from "./illustrations/usage-states-illustration";
import { SECTION_IDS } from "./links";
import { SectionHeading, TitleAccent } from "./section-heading";

/**
 * Cost you can audit (Figma "Section/Cost", v4): the per-call breakdown and its usage states,
 * reconciliation with the provider's bill, and attribution with budgets. Grid 7+5, then a full
 * width split tile from 1280px.
 */
export function CostSection() {
  return (
    <section
      id={SECTION_IDS.cost}
      aria-labelledby="cost-title"
      className="scroll-mt-6 px-5 pt-24 md:px-8 lg:pt-40"
    >
      <SectionHeading
        id="cost-title"
        overline="Cost you can audit"
        title={
          <>
            Know what each call cost, <TitleAccent>and why.</TitleAccent>
          </>
        }
        description="Spanlight prices cache writes, long-prompt tiers, batch and flex rates and web search fees from a versioned table, then sets your traced spend next to the provider’s bill."
      />

      <div className="mx-auto mt-9 grid max-w-[1200px] grid-cols-1 gap-4 md:grid-cols-2 lg:mt-14 xl:grid-cols-12 xl:gap-5">
        <FeatureTile
          tone="ink"
          overline="Cost breakdown"
          title="Unknown, never $0"
          description={{
            mobile:
              "Every model call gets a line-by-line breakdown. When a cost can’t be known, the span says why instead of showing $0.",
            desktop:
              "Every model call gets a breakdown: each token class with its quantity, rate and tier, and the price row used. When a cost can’t be known, the span says why instead of showing $0.",
          }}
          illustration={<UsageStatesIllustration />}
          className="md:col-span-2 xl:col-span-7"
        />
        <FeatureTile
          overline="Reconciliation"
          title="Checked against the bill"
          description="Connect an Anthropic or OpenAI admin key, or upload a CSV, and compare traced spend with what the provider billed, fetched every day."
          illustration={<BillingIllustration />}
          className="md:col-span-2 xl:col-span-5"
        />
        <FeatureTile
          layout="split"
          overline="Attribution & budgets"
          title="Spend by customer, feature or release"
          description={{
            mobile:
              "Group spend by any metadata key, and block gateway calls when a key’s budget runs out.",
            desktop:
              "Tag traces with up to 32 metadata keys and see spend, errors and retry cost grouped by any of them, with rows that add up to the total. Put a budget on a key like customer_id=acme and the gateway blocks calls once it runs out.",
          }}
          illustration={<AttributionIllustration />}
          className="md:col-span-2 xl:col-span-12"
        />
      </div>
    </section>
  );
}
