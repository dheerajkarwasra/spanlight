/**
 * Secrets that arrive in a link (a password-reset token, an email-verification token, a sign-in
 * challenge from the OAuth callback) are in the URL fragment: browsers send it to no server and
 * put it in no `Referer` header. These helpers read one and then take it out of the address bar and
 * the history entry, so it is not left on screen, in a bookmark or in a shared URL. They never
 * write it anywhere else: callers keep it in component state for as long as they need it. One
 * exception: the invite page keeps its token in `sessionStorage` across a signed-out sign-in.
 */

export type FragmentKey = "token" | "challenge";

/** The value of `#<key>=…` in a fragment, or null when it is missing or empty. */
export function readFragmentParam(
  key: FragmentKey,
  hash: string = window.location.hash,
): string | null {
  const params = new URLSearchParams(hash.startsWith("#") ? hash.slice(1) : hash);
  const value = params.get(key);
  return value ? value : null;
}

type FragmentWindow = Pick<Window, "history" | "location">;

/**
 * Replaces the current history entry with the same address minus the fragment. It leaves the entry's
 * state alone, which the router keeps its own bookkeeping in. Safe to call more than once.
 */
export function clearFragment(win: FragmentWindow = window): void {
  const { pathname, search, hash } = win.location;
  if (!hash) {
    return;
  }
  win.history.replaceState(win.history.state, "", `${pathname}${search}`);
}
