import { ReferenceArea } from "recharts";

import { useMediaQuery } from "@/lib/use-media-query";
import { cn } from "@/lib/utils";

import type { BandSpan } from "./incident-overlay";

/** The 12 % band is decoration only; the legend, tooltip, table and summary carry its meaning. */
const BAND_FILL = "var(--chart-band-warning-a12)";

/** 45° hatch for critical incidents, 1 px at a 6 px pitch, ≥ 3:1 in both themes. */
const HATCH_STROKE = "var(--chart-band-hatch)";

/** The critical swatch: the same hatch as the plot, drawn with CSS. */
const SWATCH_HATCH = {
  backgroundImage: `repeating-linear-gradient(45deg, ${HATCH_STROKE} 0 1px, transparent 1px 4px)`,
};

/** Render inside the chart's `<defs>`: the band fill with the hatch over it. */
export function HatchPattern({ id }: { id: string }) {
  return (
    <pattern
      id={id}
      width={6}
      height={6}
      patternUnits="userSpaceOnUse"
      patternTransform="rotate(45)"
    >
      <rect width={6} height={6} fill={BAND_FILL} />
      <line x1={0} y1={0} x2={0} y2={6} stroke={HATCH_STROKE} strokeWidth={1} />
    </pattern>
  );
}

interface IncidentBandAreasProps {
  spans: readonly BandSpan[];
  /** The `HatchPattern` id; critical bands are filled with it. */
  hatchId: string;
  yAxisId: string;
}

/**
 * One shaded `ReferenceArea` per incident behind the series, full plot height. The bands fade in
 * once, and appear at once under `prefers-reduced-motion`.
 */
export function IncidentBandAreas({ spans, hatchId, yAxisId }: IncidentBandAreasProps) {
  const reduceMotion = useMediaQuery("(prefers-reduced-motion: reduce)");
  return (
    <>
      {spans.map((span) => (
        <ReferenceArea
          key={span.key}
          yAxisId={yAxisId}
          x1={span.x1}
          x2={span.x2}
          fill={span.critical ? `url(#${hatchId})` : BAND_FILL}
          fillOpacity={1}
          stroke="none"
          ifOverflow="hidden"
          className={cn(!reduceMotion && "animate-fade-in", span.critical && "incident-band-hatch")}
        />
      ))}
    </>
  );
}

/** Legend entries for the bands: the outline is the 5.19:1 hatch colour, never fill alone. */
export function IncidentLegendItems({ hasCritical }: { hasCritical: boolean }) {
  return (
    <>
      <li className="flex items-center gap-1.5">
        <IncidentSwatch hatched={false} />
        <span>Provider incident</span>
      </li>
      {hasCritical ? (
        <li className="flex items-center gap-1.5">
          <IncidentSwatch hatched />
          <span>Critical (hatched)</span>
        </li>
      ) : null}
    </>
  );
}

/** The tooltip's key for its incident lines: a solid square in the band colour. */
export function IncidentKey() {
  return (
    <span
      aria-hidden
      className="inline-block size-2.5 shrink-0 rounded-[2px] bg-chart-band-warning"
    />
  );
}

function IncidentSwatch({ hatched }: { hatched: boolean }) {
  return (
    <span
      aria-hidden
      className="inline-block h-3 w-3.5 shrink-0 rounded-[3px] border border-chart-band-hatch bg-chart-band-warning-a12"
      style={hatched ? SWATCH_HATCH : undefined}
    />
  );
}
