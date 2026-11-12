import { Figure, Section, SectionHeading, TitleSecondLine } from "./landing-parts";
import { stagger } from "./motion";
import { SECTION_IDS } from "./links";

/*
 * Worked example from the Spanlight price table, labelled as such on the receipt:
 * claude-sonnet-5-5 at $2.00 / $10.00 / $0.10 per 1M input, output and cache-read tokens.
 */
const RECEIPT_LINES = [
  { label: "Input", basis: "1,840 tok × $2.00 / 1M", cost: "$0.003680" },
  { label: "Cache read", basis: "6,200 tok × $0.10 / 1M", cost: "$0.000620" },
  { label: "Output", basis: "412 tok × $10.00 / 1M", cost: "$0.004120" },
] as const;

const RECEIPT_TOTAL = "$0.008420";

/** Spans whose cost cannot be known, with the reason Spanlight records. */
const UNKNOWN_SPANS = [
  { span: "stream cut before usage", reason: "usage_not_reported" },
  { span: "llama-3.1-8b-instruct", reason: "no_price" },
] as const;

const RECEIPT_ALT = `Cost receipt for the answer span on claude-sonnet-5-5: ${RECEIPT_LINES.map(
  (line) => `${line.label} ${line.basis}, ${line.cost}`,
).join("; ")}; total ${RECEIPT_TOTAL}. Two more spans show no cost: ${UNKNOWN_SPANS.map(
  (unknown) => `${unknown.span}, ${unknown.reason}`,
).join("; ")}.`;

const FACTS = [
  { value: "32", text: "metadata pairs per trace, for attribution by customer, feature or team" },
  { value: "6", text: "budget scopes that can alert on spend, or block it" },
  { value: "—", text: "what an unknown cost shows, never $0" },
] as const;

/** The phone frame replaces the bill check and the facts with four short claims. */
const CLAIMS = [
  {
    title: "Unknown stays unknown",
    text: "Prices come from a versioned table that covers cache writes, long-prompt tiers, batch, flex and priority rates, and web search fees. A cost that cannot be known shows as — with the reason, never as $0.",
  },
  {
    title: "Checked against the bill",
    text: "Connect an Anthropic or OpenAI admin key, or upload a CSV. Spanlight compares traced spend with what the provider billed, every day.",
  },
  {
    title: "Spend, by what caused it",
    text: "Tag traces with up to 32 metadata pairs, such as customer, feature or team, and see spend, calls and errors for each value.",
  },
  {
    title: "Budgets that alert or block",
    text: "Set a budget per project, gateway key, user, model, metadata pair or trace name. Each one can alert on spend, or block it.",
  },
] as const;

const CARD = "rounded-md border border-mono-line-strong bg-mono-white";

/** "Price": the cost receipt, the bill check and the attribution and budget facts (Figma "Price"). */
export function PriceSection() {
  return (
    <Section id={SECTION_IDS.price} tone="paper">
      <SectionHeading
        id={SECTION_IDS.price}
        label="Price"
        title={<TitleSecondLine first="Priced line by line." second="Checked against the bill." />}
        description="A cost that cannot be known shows as — with its reason, never as $0."
        mobileDescription={null}
      />

      <div className="flex w-full flex-col gap-7 xl:flex-row xl:items-stretch xl:gap-6">
        <Figure
          label={RECEIPT_ALT}
          className={`${CARD} flex flex-col gap-[22px] px-5 pt-6 pb-5 xl:w-[480px] xl:shrink-0 xl:px-8 xl:pt-8 xl:pb-7`}
        >
          <div className="flex flex-col gap-1 font-mono leading-[1.4]">
            <span className="text-sm font-medium text-mono-900 xl:text-[15px]">answer</span>
            <span className="text-xs text-mono-500">llm span · claude-sonnet-5-5</span>
          </div>
          <div className="flex flex-col border-t border-mono-line leading-[1.4]">
            {RECEIPT_LINES.map((line, index) => (
              <div
                key={line.label}
                style={stagger(index)}
                className="lp-rise flex items-center justify-between border-b border-mono-line py-3.5 [--lp-rise:8px] [--lp-stagger:120ms]"
              >
                <span className="flex flex-col gap-0.5">
                  <span className="text-sm font-medium text-mono-900 xl:text-[15px]">
                    {line.label}
                  </span>
                  <span className="font-mono text-xs text-mono-500">{line.basis}</span>
                </span>
                <span className="font-mono text-xs text-mono-900 xl:text-sm">{line.cost}</span>
              </div>
            ))}
          </div>
          <div
            style={stagger(RECEIPT_LINES.length)}
            className="lp-fade flex items-end justify-between font-medium text-mono-900 [--lp-stagger:120ms]"
          >
            <span className="text-sm leading-[1.4] xl:text-[15px]">Total</span>
            <span className="text-[32px] leading-none tracking-[-0.035em] tabular-nums xl:text-[44px]">
              {RECEIPT_TOTAL}
            </span>
          </div>
          <div className="flex flex-col gap-2.5 border-t border-mono-line pt-5 leading-[1.4]">
            <span className="text-xs text-mono-500 xl:text-[13px]">
              Two more spans, cost unknown
            </span>
            {UNKNOWN_SPANS.map((unknown) => (
              <div
                key={unknown.reason}
                className="flex flex-col font-mono text-xs xl:flex-row xl:justify-between"
              >
                <span className="text-mono-600">{unknown.span}</span>
                <span className="font-medium text-mono-900">— {unknown.reason}</span>
              </div>
            ))}
          </div>
          <span className="text-xs leading-[1.4] text-mono-500">
            Worked example at list rates from the Spanlight price table.
          </span>
        </Figure>

        <div className="hidden min-w-0 flex-1 flex-col gap-6 xl:flex">
          <Figure
            label="Reconciliation: spend traced by Spanlight and spend billed by the provider, drawn as two equal bars and compared every day. Illustration, no values shown."
            className={`${CARD} flex flex-1 flex-col justify-between p-7`}
          >
            <div className="flex justify-between font-mono leading-[1.4]">
              <span className="text-[13px] font-medium text-mono-900">reconciliation</span>
              <span className="text-xs text-mono-500">compared every day</span>
            </div>
            <div className="flex flex-col gap-[18px]">
              {[
                { label: "Traced by Spanlight", bar: "bg-mono-900" },
                { label: "Billed by the provider", bar: "bg-mono-400" },
              ].map((row, index) => (
                <div key={row.label} className="flex flex-col gap-2">
                  <span className="text-[15px] leading-[1.4] font-medium text-mono-900">
                    {row.label}
                  </span>
                  <span className="block h-3.5 overflow-hidden rounded-sm bg-mono-mist">
                    <span
                      style={stagger(index * 2)}
                      className={`lp-grow block h-full w-[84.6%] rounded-sm ${row.bar}`}
                    />
                  </span>
                </div>
              ))}
            </div>
            <span className="text-[13px] leading-[1.45] text-mono-500">
              From an Anthropic or OpenAI admin key, or a CSV upload. Illustration, no values shown.
            </span>
          </Figure>
          <ul className="flex gap-6">
            {FACTS.map((fact) => (
              <li key={fact.value} className={`${CARD} flex flex-1 flex-col gap-2.5 p-[22px]`}>
                <span className="text-[44px] leading-none font-medium tracking-[-0.035em] text-mono-900">
                  {fact.value}
                </span>
                <span className="text-sm leading-[1.45] text-mono-600">{fact.text}</span>
              </li>
            ))}
          </ul>
        </div>

        <ul className="flex flex-col xl:hidden">
          {CLAIMS.map((claim, index) => (
            <li
              key={claim.title}
              className={`flex flex-col gap-2 border-t py-5 ${index === 0 ? "border-mono-900" : "border-mono-line-strong"}`}
            >
              <h3 className="text-[19px] leading-[1.3] font-medium tracking-[-0.02em] text-mono-900">
                {claim.title}
              </h3>
              <p className="text-[15px] leading-[1.55] text-mono-600">{claim.text}</p>
            </li>
          ))}
        </ul>
      </div>
    </Section>
  );
}
