import { useEffect, useRef } from "react";

import { keyTargetOf, shortcutFor, type ShortcutAction } from "./shortcuts";

/**
 * Listens for the work-mode keys on the whole window and hands each recognised action to
 * `onAction`. A key that triggers an action does nothing else (a letter typed in a number input
 * is not inserted, Enter does not submit a form twice).
 */
export function useWorkShortcuts(helpOpen: boolean, onAction: (action: ShortcutAction) => void) {
  const latest = useRef({ helpOpen, onAction });
  useEffect(() => {
    latest.current = { helpOpen, onAction };
  });

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.defaultPrevented || event.isComposing || event.repeat) {
        return;
      }
      const action = shortcutFor(event, keyTargetOf(event.target), latest.current.helpOpen);
      if (action === null) {
        return;
      }
      event.preventDefault();
      latest.current.onAction(action);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
    };
  }, []);
}
