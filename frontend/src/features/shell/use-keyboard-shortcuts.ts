import { useNavigate } from "@tanstack/react-router";
import { useEffect } from "react";

import { isNavigationLocked } from "@/lib/navigation-lock";

import { NAV_ITEMS } from "./nav-items";
import { useProjectParams } from "./project-context";

const SEQUENCE_TIMEOUT_MS = 1000;

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false;
  }
  return (
    target.isContentEditable ||
    target.tagName === "INPUT" ||
    target.tagName === "TEXTAREA" ||
    target.tagName === "SELECT"
  );
}

/**
 * Global shortcuts: ⌘K / Ctrl+K toggles the command palette, and Linear-style "G then <key>"
 * sequences jump between pages. The keys come from `NAV_ITEMS`: O T S D V (provider health) R U G A B ",", and for the
 * Improve pages P (prompts), L (playground), N (datasets), Q (review queues), E (evaluators) and
 * X (experiments).
 */
export function useKeyboardShortcuts(onTogglePalette: () => void): void {
  const navigate = useNavigate();
  const params = useProjectParams();

  useEffect(() => {
    let awaitingSecondKey = false;
    let timeoutId: number | undefined;

    function onKeyDown(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        // A screen that must not be left (recovery codes) keeps the palette closed.
        if (!isNavigationLocked()) {
          onTogglePalette();
        }
        return;
      }

      if (isNavigationLocked()) {
        awaitingSecondKey = false;
        return;
      }

      if (event.metaKey || event.ctrlKey || event.altKey || isTypingTarget(event.target)) {
        return;
      }

      const key = event.key.toLowerCase();
      if (awaitingSecondKey) {
        awaitingSecondKey = false;
        window.clearTimeout(timeoutId);
        const item = NAV_ITEMS.find((candidate) => candidate.shortcut === `G ${key.toUpperCase()}`);
        if (item) {
          event.preventDefault();
          void navigate({ to: item.to, params });
        }
        return;
      }

      if (key === "g") {
        awaitingSecondKey = true;
        timeoutId = window.setTimeout(() => {
          awaitingSecondKey = false;
        }, SEQUENCE_TIMEOUT_MS);
      }
    }

    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.clearTimeout(timeoutId);
    };
  }, [navigate, onTogglePalette, params]);
}
