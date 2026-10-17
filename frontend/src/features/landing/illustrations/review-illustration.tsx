import { IlloHeader, IlloRow, type DotTone } from "./illo-parts";

/** The reasons the nightly review policy picks a trace for the queue. */
const REASONS: readonly { label: string; dot: DotTone }[] = [
  { label: "Doctor finding", dot: "warning" },
  { label: "error", dot: "danger" },
  { label: "cost above p99", dot: "cost" },
  { label: "random ≤ 5 %", dot: "none" },
];

/** What lands in the daily review queue, and the score it asks for (Figma "Illo/Review"). */
export function ReviewIllustration() {
  return (
    <div className="flex flex-col gap-2">
      <IlloHeader label="Daily · 01:00 UTC" />
      {REASONS.map((reason) => (
        <IlloRow key={reason.label} dot={reason.dot} label={reason.label} mono={false} />
      ))}
      <IlloRow tone="outline" dot="accent" label="failure_category" />
    </div>
  );
}
