import { IlloHeader, IlloRow, Pill } from "./illo-parts";

/**
 * Two runs compared with an interval and a significance test, then the CI gate (Figma
 * "Illo/Evals v4"). The figures are the example the changelog uses for the interval format.
 */
export function EvalsIllustration() {
  return (
    <div className="flex flex-col gap-2">
      <IlloHeader label="Run A vs run B">
        <Pill>same dataset</Pill>
      </IlloHeader>
      <IlloRow label="relevance">
        <span className="text-xs font-semibold whitespace-nowrap">
          78% <span className="hidden md:inline">(95% CI 65–88%, n = 50)</span>
        </span>
      </IlloRow>
      <IlloRow label="McNemar exact test">
        <Pill tone="warning" className="hidden md:inline">
          not significant at 0.05
        </Pill>
      </IlloRow>
      <IlloRow tone="ink" dot="lime" label="eval-gate · pull request">
        <span className="text-xs font-medium whitespace-nowrap">mean ≥ threshold ✓</span>
      </IlloRow>
    </div>
  );
}
