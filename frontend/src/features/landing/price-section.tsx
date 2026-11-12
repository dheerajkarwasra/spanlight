import { CircleCheck } from "lucide-react";

import { Figure, Section, SectionHeading, TitleSecondLine } from "./landing-parts";
import { stagger } from "./motion";
import { SECTION_IDS } from "./links";

/*
 * One real span from the demo support bot, as the product's Cost tab shows it (the 2026-11-05
 * product recording): claude-sonnet-5-5 priced from price row 2026-10-25.
 */
const COST_SPAN = "chat claude-sonnet-5-5";
const PRICE_ROW = "2026-10-25";

const COST_LINES = [
  { label: "Uncached input", tokens: "443", rate: "$2.00 / MTok", cost: "$0.000886" },
  { label: "Cache read", tokens: "0", rate: "$0.10 / MTok", cost: "$0" },
  { label: "Cache write", tokens: "0", rate: "$2.50 / MTok", cost: "$0" },
  { label: "Cache write · 1 h", tokens: "0", rate: "$4.00 / MTok", cost: "$0" },
  { label: "Output", tokens: "300", rate: "$10.00 / MTok", cost: "$0.003000" },
] as const;

const COST_TOTAL = "$0.003886";

/** Spans whose cost cannot be known, with the reason Spanlight records. */
const UNKNOWN_SPANS = [
  { span: "stream cut before usage", reason: "usage_not_reported" },
  { span: "llama-3.1-8b-instruct", reason: "no_price" },
] as const;

const COST_ALT = `Cost breakdown for the span ${COST_SPAN}, recomputed from stored usage and price row ${PRICE_ROW}: ${COST_LINES.map(
  (line) => `${line.label}, ${line.tokens} tokens at ${line.rate}, ${line.cost}`,
).join(
  "; ",
)}; recomputed total ${COST_TOTAL}, which matches the cost stored on the span. Two other spans show no cost: ${UNKNOWN_SPANS.map(
  (unknown) => `${unknown.span}, ${unknown.reason}`,
).join("; ")}.`;

/** Zero lines read quieter than the lines that cost something. */
function tone(value: string) {
  return value === "0" || value === "$0" ? "text-mono-600" : "text-mono-900";
}

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

/** "Price": the cost breakdown, the bill check and the attribution and budget facts (Figma "Price"). */
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
          label={COST_ALT}
          className={`${CARD} flex flex-col gap-3.5 px-[18px] pt-5 pb-[18px] xl:w-[560px] xl:shrink-0 xl:gap-[18px] xl:px-7 xl:pt-7 xl:pb-6`}
        >
          <div className="flex flex-col items-start gap-2.5 xl:flex-row xl:items-center xl:justify-between">
            <div className="flex flex-col gap-[3px] font-mono leading-[1.4]">
              <span className="text-[13px] font-medium text-mono-900 xl:text-sm">{COST_SPAN}</span>
              <span className="text-xs text-mono-600">llm span · anthropic · standard</span>
            </div>
            <span className="flex items-center gap-1.5 rounded-full border border-mono-line-strong bg-mono-mist py-[5px] pr-2.5 pl-2 text-xs leading-[1.4] font-medium text-mono-900">
              <CircleCheck className="size-3.5" />
              Matches
            </span>
          </div>
          <span className="text-[13px] leading-[1.4] text-mono-600">
            Recomputed from stored usage and price row {PRICE_ROW}
          </span>
          <div className="flex flex-col leading-[1.4]">
            <div className="hidden gap-3 border-b border-mono-line pb-2 font-mono text-xs text-mono-600 xl:flex">
              <span className="flex-1">Class</span>
              <span className="w-16 text-right">Tokens</span>
              <span className="w-[120px] text-right">Rate</span>
              <span className="w-9 text-right">×</span>
              <span className="w-24 text-right">USD</span>
            </div>
            {COST_LINES.map((line, index) => (
              <div
                key={line.label}
                style={stagger(index)}
                className="lp-rise flex items-center justify-between gap-3 border-b border-mono-line py-2.5 [--lp-rise:8px] [--lp-stagger:120ms] xl:py-[11px]"
              >
                <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                  <span className="text-sm font-medium text-mono-900">{line.label}</span>
                  <span className="font-mono text-xs text-mono-600 xl:hidden">
                    {line.tokens} tok × {line.rate}
                  </span>
                </span>
                <span
                  className={`hidden w-16 text-right font-mono text-xs xl:block ${tone(line.tokens)}`}
                >
                  {line.tokens}
                </span>
                <span className="hidden w-[120px] text-right font-mono text-xs text-mono-900 xl:block">
                  {line.rate}
                </span>
                <span className="hidden w-9 text-right font-mono text-xs text-mono-900 xl:block">
                  ×1
                </span>
                <span
                  className={`w-[74px] text-right font-mono text-xs xl:w-24 ${tone(line.cost)}`}
                >
                  {line.cost}
                </span>
              </div>
            ))}
          </div>
          <div
            style={stagger(COST_LINES.length)}
            className="lp-fade flex flex-col gap-1.5 [--lp-stagger:120ms]"
          >
            <div className="flex items-end justify-between font-medium text-mono-900">
              <span className="text-sm leading-[1.4] xl:text-[15px]">Recomputed</span>
              <span className="text-[30px] leading-none tracking-[-0.035em] tabular-nums xl:text-[40px]">
                {COST_TOTAL}
              </span>
            </div>
            <div className="flex justify-between leading-[1.4] text-mono-600">
              <span className="text-[13px]">Stored on the span</span>
              <span className="font-mono text-xs">{COST_TOTAL}</span>
            </div>
          </div>
          <div className="flex flex-col gap-2 border-t border-mono-line pt-4 leading-[1.4]">
            <span className="text-[13px] text-mono-600">
              When a cost cannot be known, the span says why
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
          <span className="text-xs leading-[1.4] text-mono-600">
            A real span from the demo support bot, as the Cost tab shows it.
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
