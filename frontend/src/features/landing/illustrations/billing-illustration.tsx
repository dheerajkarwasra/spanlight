import { IlloHeader, IlloNote, IlloRow, Pill } from "./illo-parts";

/** The three kinds of billing source and how each is read (Figma "Illo/Billing"). */
export function BillingIllustration() {
  return (
    <div className="flex flex-col gap-2">
      <IlloHeader label="Provider bill">
        <Pill>reconciled daily</Pill>
      </IlloHeader>
      <IlloRow dot="llm" label="Anthropic Admin API">
        <Pill tone="success">03:30 UTC</Pill>
      </IlloRow>
      <IlloRow dot="llm" label="OpenAI admin key">
        <Pill tone="success">daily</Pill>
      </IlloRow>
      <IlloRow tone="outline" dot="tool" label="CSV upload">
        <Pill>10 columns</Pill>
      </IlloRow>
      <IlloNote>Traced spend next to what you were billed.</IlloNote>
    </div>
  );
}
