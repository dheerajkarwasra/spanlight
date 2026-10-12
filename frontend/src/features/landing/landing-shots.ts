/**
 * Real product screenshots on the landing page. Each one is captured from the live demo
 * workspace by `scripts/capture-landing.ts` (rerun before every release) in the light and dark
 * theme at 1x and 2x, and stored as `src/assets/landing/<id>-<theme>@<scale>x.webp`.
 *
 * The capture script imports SHOTS too, so the crop sizes here are the sizes it writes.
 */

export type ShotId = "hero" | "doctor" | "gateway" | "prompts" | "evals" | "alerts";
export type ShotTheme = "light" | "dark";

export interface ShotSpec {
  /** Width and height of the 1x image in CSS pixels; the 2x file is twice that. */
  width: number;
  height: number;
  /** What the screenshot shows, for people who can't see it. */
  alt: string;
  /** The `sizes` attribute: how wide the image is drawn at each breakpoint. */
  sizes: string;
}

export const SHOTS: Record<ShotId, ShotSpec> = {
  hero: {
    width: 1280,
    height: 800,
    alt: "Spanlight's experiment comparison: two runs of the same dataset with different prompt versions, their average scores side by side and the items whose scores changed.",
    sizes: "(min-width: 1240px) 1180px, calc(100vw - 52px)",
  },
  doctor: {
    width: 636,
    height: 294,
    alt: "An open Doctor insight: the detector that fired, how often, the evidence traces, the suggested fix and how to check it.",
    sizes: "(min-width: 1280px) 636px, (min-width: 768px) calc(100vw - 120px), calc(100vw - 84px)",
  },
  gateway: {
    width: 432,
    height: 303,
    alt: "The gateway overview: requests, errors, cache hits and spend through the gateway.",
    sizes: "(min-width: 1280px) 432px, (min-width: 768px) calc(50vw - 84px), calc(100vw - 84px)",
  },
  prompts: {
    width: 331,
    height: 300,
    alt: "A prompt's versions, with the label each one carries and the changes between two versions.",
    sizes: "(min-width: 1280px) 331px, (min-width: 768px) calc(50vw - 84px), calc(100vw - 84px)",
  },
  evals: {
    width: 432,
    height: 300,
    alt: "An LLM judge evaluator: its spend this month against its budget and its recent runs.",
    sizes: "(min-width: 1280px) 432px, (min-width: 768px) calc(50vw - 84px), calc(100vw - 84px)",
  },
  alerts: {
    width: 229,
    height: 301,
    alt: "An alert rule: its condition, current state and recent events.",
    sizes: "(min-width: 1280px) 229px, (min-width: 768px) calc(50vw - 84px), calc(100vw - 84px)",
  },
};

export const SHOT_IDS = Object.keys(SHOTS) as ShotId[];
export const SHOT_THEMES: readonly ShotTheme[] = ["light", "dark"];
export const SHOT_SCALES = [1, 2] as const;

/** Largest allowed file at 2x, in bytes. */
export function shotBudgetBytes(id: ShotId): number {
  return id === "hero" ? 250 * 1024 : 80 * 1024;
}

/** File name of one capture, e.g. "doctor-dark@2x.webp". */
export function shotFileName(id: ShotId, theme: ShotTheme, scale: 1 | 2): string {
  return `${id}-${theme}@${scale}x.webp`;
}

export interface ShotSources {
  src: string;
  srcSet: string;
}

/**
 * The `src`/`srcSet` pair for one screenshot in one theme, from a map of file name to URL.
 * Returns null unless both scales exist, so a missing capture renders nothing rather than
 * a broken image.
 */
export function shotSources(
  id: ShotId,
  theme: ShotTheme,
  urls: Readonly<Record<string, string>>,
): ShotSources | null {
  const one = urls[shotFileName(id, theme, 1)];
  const two = urls[shotFileName(id, theme, 2)];
  if (!one || !two) {
    return null;
  }
  const { width } = SHOTS[id];
  return { src: one, srcSet: `${one} ${width}w, ${two} ${width * 2}w` };
}
