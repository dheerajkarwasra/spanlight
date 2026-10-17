import { IlloHeader, IlloRow, Pill } from "./illo-parts";

/** A prompt's versions with their labels, and what you can do with them (Figma "Illo/Prompts v4"). */
export function PromptsIllustration() {
  return (
    <div className="flex flex-col gap-2">
      <IlloHeader label={<span className="font-mono normal-case">support/answer</span>}>
        <Pill>chat</Pill>
      </IlloHeader>
      <IlloRow label="v3" mono={false}>
        <Pill tone="lime">production</Pill>
      </IlloRow>
      <IlloRow label="v2" mono={false}>
        <Pill tone="accent">staging</Pill>
      </IlloRow>
      <IlloRow label="v1" mono={false} />
      <div className="flex flex-wrap gap-2 pt-1">
        <Pill tone="ink">Open in playground →</Pill>
        <Pill tone="accent">Replay a span</Pill>
      </div>
    </div>
  );
}
