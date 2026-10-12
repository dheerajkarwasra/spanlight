import { useRef } from "react";

/**
 * The control that opened a dialog. Opened from a dropdown item, that is the menu's trigger (the
 * "more" button): the item itself is gone with the menu.
 */
function openerOf(active: Element | null): HTMLElement | null {
  const menu = active?.closest('[role="menu"]');
  const opener = menu?.id
    ? (document.querySelector(`[aria-controls="${CSS.escape(menu.id)}"]`) ?? active)
    : active;
  return opener instanceof HTMLElement ? opener : null;
}

/**
 * Focus handlers for a controlled dialog with no trigger of its own, opened from a menu item:
 * Radix would return focus to the vanished item, so it falls to the page (WCAG 2.4.3). These
 * remember the menu's trigger on open and focus it again on close. Same rule as `ConfirmDialog`.
 */
export function useReturnFocus() {
  const opener = useRef<HTMLElement | null>(null);
  return {
    onOpenAutoFocus: () => {
      opener.current = openerOf(document.activeElement);
    },
    onCloseAutoFocus: (event: Event) => {
      const button = opener.current;
      if (button?.isConnected) {
        event.preventDefault();
        button.focus();
      }
    },
  };
}
