/** A group's share of the window's cost, as text and as the width of its bar. */

const SHARE_FORMAT = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

/** Below this a share would round to 0.0 %, which reads as "free". */
const SMALLEST_SHOWN = 0.001;

/** `0.612` → "61.2%"; a share under 0.1 % → "<0.1%"; unknown → "—", never 0. */
export function formatShare(share: number | null): string {
  if (share === null || !Number.isFinite(share)) {
    return "—";
  }
  if (share > 0 && share < SMALLEST_SHOWN) {
    return "<0.1%";
  }
  return `${SHARE_FORMAT.format(share * 100)}%`;
}

/** The bar's CSS width on its track; a tiny share keeps 1 px so it never looks like none. */
export function shareBarWidth(share: number | null): string | null {
  if (share === null || !Number.isFinite(share)) {
    return null;
  }
  if (share > 0 && share < SMALLEST_SHOWN) {
    return "1px";
  }
  return `${Math.round(Math.min(share, 1) * 1000) / 10}%`;
}
