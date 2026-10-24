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
 * sequences jump between pages. The keys come from the hub tabs in `NAV_ITEMS`: O (overview),
 * T S U (traces, sessions, users), D V R A (doctor, provider health, releases, alerts), Q N X E
 * (review, datasets, experiments, evaluators), P L (prompts, playground), C B (spend, budgets),
 * G (gateway) and "," (settings). `?` opens the keyboard help.
 */
export function useKeyboardShortcuts(onTogglePalette: () => void, onOpenHelp: () => void): void {
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

      // A screen that must not be left, or an open modal (the palette, the help, a confirm), owns
      // the keyboard: no help on top of it and no page change behind it.
      if (
        isNavigationLocked() ||
        document.querySelector('[role="dialog"], [role="alertdialog"]') !== null
      ) {
        awaitingSecondKey = false;
        return;
      }

      if (event.metaKey || event.ctrlKey || event.altKey || isTypingTarget(event.target)) {
        return;
      }

      if (event.key === "?") {
        awaitingSecondKey = false;
        event.preventDefault();
        onOpenHelp();
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
  }, [navigate, onOpenHelp, onTogglePalette, params]);
}
