import { useChartView, useStoredChoice } from "@/lib/use-stored-choice";

/** Per viewer, per browser: whether the calls/errors chart shades provider incidents. */
export const INCIDENT_OVERLAY_KEY = "spanlight-incident-overlay";

/** Per viewer, per browser: whether the calls/errors card opens as its table. */
export const CALLS_VIEW_KEY = "spanlight-overview-calls-view";

const OVERLAY_CHOICES = ["on", "off"] as const;

/** "Show incidents": on unless this viewer turned it off. */
export function useIncidentOverlay(): [boolean, (shown: boolean) => void] {
  const [value, setValue] = useStoredChoice(INCIDENT_OVERLAY_KEY, OVERLAY_CHOICES, "on");
  return [value === "on", (shown) => setValue(shown ? "on" : "off")];
}

/** "Show table": the chart unless this viewer left the card on its table. */
export function useCallsView() {
  return useChartView(CALLS_VIEW_KEY);
}
