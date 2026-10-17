/**
 * The wait after a `429 RATE_LIMITED` refresh. The server allows one refresh per source every
 * 5 minutes (bursts of 2) and says how long to wait in `Retry-After`; the button counts down to
 * that moment and stays disabled until then.
 */

/** The server's refresh interval, used when a 429 arrives without `Retry-After`. */
export const DEFAULT_RETRY_SECONDS = 300;

/** The moment (epoch ms) a refresh may be tried again, from the 429's `Retry-After`. */
export function retryDeadline(retryAfterSeconds: number | null, now: number): number {
  return now + (retryAfterSeconds ?? DEFAULT_RETRY_SECONDS) * 1000;
}

/**
 * "4:12" while there is time left before `deadline`, rounded up to the second so it never reads
 * "0:00" while still waiting; null once the wait is over.
 */
export function retryAfterLabel(deadline: number, now: number): string | null {
  const remaining = Math.ceil((deadline - now) / 1000);
  if (remaining <= 0) {
    return null;
  }
  const minutes = Math.floor(remaining / 60);
  const seconds = remaining % 60;
  return `${String(minutes)}:${String(seconds).padStart(2, "0")}`;
}
