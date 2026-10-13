/**
 * Fitting KPI values to their tile without truncating them.
 *
 * A metric value is never cut short ("100. …" says nothing). Instead every value in a grid of
 * equal tiles shares one font size: the metric token, or smaller when the widest value would not
 * fit. The width comes from a per-glyph estimate of the metric face (Plus Jakarta Sans ExtraBold
 * at the Metric/L tracking), so the size is pure CSS and needs no measuring in the browser.
 */

/** Advance widths in em, measured in Figma. Unlisted glyphs use DEFAULT_EM. */
const GLYPH_EM: Record<string, number> = {
  "0": 0.67,
  "1": 0.38,
  "2": 0.56,
  "3": 0.58,
  "4": 0.63,
  "5": 0.58,
  "6": 0.57,
  "7": 0.53,
  "8": 0.6,
  "9": 0.57,
  ",": 0.35,
  ".": 0.37,
  "%": 1.04,
  " ": 0.2,
  " ": 0.2,
  m: 0.89,
  s: 0.48,
  h: 0.56,
  d: 0.64,
  k: 0.55,
  M: 0.88,
  B: 0.66,
  "—": 0.98,
  $: 0.61,
  "-": 0.64,
  "<": 0.64,
};

const DEFAULT_EM = 0.7;

/** Covers kerning and rounding, so the estimate never falls short of the rendered width. */
const SAFETY = 1.05;

/** The estimated width of `text` set in the metric face, in em. */
export function metricEmWidth(text: string): number {
  let em = 0;
  for (const glyph of text) {
    em += GLYPH_EM[glyph] ?? DEFAULT_EM;
  }
  return em * SAFETY;
}

/**
 * The CSS font size for a grid of metric values: the metric token, capped so the widest value
 * fits its container's inline size (the element carrying it must sit in a size container).
 * Unknown values render as a dash, which is narrower than any number, so they are skipped.
 * Undefined when there is nothing to fit.
 */
export function fittedMetricFontSize(values: readonly (string | null)[]): string | undefined {
  const widest = Math.max(0, ...values.map((value) => (value ? metricEmWidth(value) : 0)));
  if (widest === 0) {
    return undefined;
  }
  return `min(var(--text-metric), calc(100cqi / ${widest.toFixed(2)}))`;
}
