/**
 * After a deploy, a tab opened on the previous build still asks for its old hashed chunks, which
 * the new build no longer serves, so lazily loaded pages and dialogs fail and clicks seem to do
 * nothing. Vite reports that as `vite:preloadError`; reloading once fetches the new build. A
 * second failure within a minute is a chunk that is really missing, and must not loop.
 */
export const RELOAD_KEY = "spanlight-stale-chunk-reload";
const RELOAD_WINDOW_MS = 60_000;

interface Deps {
  storage: Pick<Storage, "getItem" | "setItem">;
  reload: () => void;
  now: () => number;
}

export function handleStaleChunk(event: { preventDefault: () => void }, deps: Deps): void {
  const last = deps.storage.getItem(RELOAD_KEY);
  if (last !== null && deps.now() - Number(last) < RELOAD_WINDOW_MS) {
    return;
  }
  deps.storage.setItem(RELOAD_KEY, String(deps.now()));
  event.preventDefault();
  deps.reload();
}

export function reloadOnStaleChunk(): void {
  window.addEventListener("vite:preloadError", (event) => {
    handleStaleChunk(event, {
      storage: window.sessionStorage,
      reload: () => {
        window.location.reload();
      },
      now: Date.now,
    });
  });
}
