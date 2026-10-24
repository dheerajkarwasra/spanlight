import {
  BASELINE_PERIOD,
  bulletPositions,
  bulletSentence,
  formatBulletValue,
  type Bullet,
} from "./evidence-bullet";

/**
 * Figma "Doctor/Evidence bullet": the observed value as a bar on a muted track, the baseline as
 * a 2 px tick, both values and the baseline's period labelled underneath. The whole chart is one
 * image named by the full sentence, so the labels below are hidden from assistive technology.
 */
export function EvidenceBulletChart({ bullet }: { bullet: Bullet }) {
  const positions = bulletPositions(bullet);
  return (
    <div className="flex flex-col gap-3">
      <div
        role="img"
        aria-label={bulletSentence(bullet)}
        className="relative h-7 overflow-hidden rounded-sm bg-surface-muted"
      >
        <div
          className="absolute inset-y-2 left-0 rounded-r bg-chart-2"
          style={{ width: `${String(positions.observed)}%` }}
        />
        <div
          className="absolute inset-y-0 w-0.5 -translate-x-1/2 bg-muted-foreground"
          style={{ left: `${String(positions.baseline)}%` }}
        />
      </div>
      <ul aria-hidden className="flex flex-wrap gap-x-5 gap-y-1 text-xs text-foreground tabular">
        <li className="flex items-center gap-1.5">
          <span className="h-2 w-3 rounded-[2px] bg-chart-2" />
          Observed {formatBulletValue(bullet.observed, bullet.unit)}
        </li>
        <li className="flex items-center gap-1.5">
          <span className="h-3 w-0.5 bg-muted-foreground" />
          Baseline {formatBulletValue(bullet.baseline, bullet.unit)}
        </li>
        <li className="text-muted-foreground">Baseline from {BASELINE_PERIOD}</li>
      </ul>
    </div>
  );
}
