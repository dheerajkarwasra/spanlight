import { IlloHeader, IlloRow, Pill } from "./illo-parts";

/** Real detector kinds from the catalogue, with the layer each points at and its certainty. */
const FINDINGS = [
  { kind: "retry_after_ignored", layer: "client", certainty: "Measured" },
  { kind: "rate_limit_pressure", layer: "provider", certainty: "Measured" },
  { kind: "context_growth", layer: "request", certainty: "Inferred" },
  { kind: "tool_loop", layer: "agent", certainty: "Measured" },
] as const;

/** Doctor findings with their layer, and the actions every insight carries (Figma "Illo/Doctor v4"). */
export function DoctorIllustration() {
  return (
    <div className="flex flex-col gap-2">
      <IlloHeader label="15 detectors · 5 layers" onInk>
        <Pill tone="rail">Was this right?</Pill>
      </IlloHeader>
      {FINDINGS.map((finding) => (
        <IlloRow key={finding.kind} tone="rail" dot="warning" label={finding.kind}>
          <Pill tone="rail" mono>
            {finding.layer}
          </Pill>
          <span className="hidden text-xs font-medium text-rail-muted-foreground md:inline">
            {finding.certainty}
          </span>
        </IlloRow>
      ))}
      <div className="flex flex-wrap gap-2 pt-1">
        <Pill tone="rail">Evidence traces</Pill>
        <Pill tone="lime">Fix</Pill>
        <Pill tone="accent">Check</Pill>
      </div>
    </div>
  );
}
