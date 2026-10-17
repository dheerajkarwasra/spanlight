import { IlloNote, IlloRow, Pill } from "./illo-parts";

/** One base URL in front of the providers: routing, fallback, cache and a budget (Figma "Illo/Gateway v4"). */
export function GatewayIllustration() {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2 pb-1">
        <Pill mono>your app</Pill>
        <span className="text-sm font-semibold text-subtle-foreground">→</span>
        <Pill tone="lime" mono>
          base_url = …/gw/v1
        </Pill>
      </div>
      <IlloRow dot="llm" label="anthropic · claude-sonnet-5-5">
        <Pill tone="success">200</Pill>
      </IlloRow>
      <IlloRow dot="llm" label="openai · gpt-4.1-mini">
        <Pill tone="accent">fallback</Pill>
      </IlloRow>
      <IlloRow dot="tool" label="cache · same request">
        <Pill tone="success">hit</Pill>
      </IlloRow>
      <IlloRow dot="warning" label="budget · customer_id=acme">
        <Pill tone="danger">402 blocked</Pill>
      </IlloRow>
      <IlloNote>Same SDK. Every call traced and priced.</IlloNote>
    </div>
  );
}
