/**
 * The SDK's console output. Warnings and errors always print; debug lines only when the
 * `SPANLIGHT_DEBUG` environment variable is `1`, `true`, `yes` or `on`.
 */

const PREFIX = "[spanlight]";

function debugEnabled(): boolean {
  return /^(1|true|yes|on)$/i.test(process.env.SPANLIGHT_DEBUG ?? "");
}

export const log = {
  debug(...args: unknown[]): void {
    if (debugEnabled()) console.debug(PREFIX, ...args);
  },
  warn(...args: unknown[]): void {
    console.warn(PREFIX, ...args);
  },
  error(...args: unknown[]): void {
    console.error(PREFIX, ...args);
  },
};

/** Run `fn`, logging and swallowing anything it throws: SDK code must never break the caller. */
export function safely(what: string, fn: () => void): void {
  try {
    fn();
  } catch (error) {
    log.debug(`Failed to ${what}`, error);
  }
}
