import { type MouseEvent, type PointerEvent, useRef, useState } from "react";

/**
 * Makes a touch tap toggle a Radix tooltip. Radix opens tooltips on hover and focus only, so on
 * a phone a tap would never show the copy. Spread `rootProps` on `TooltipRoot` and
 * `triggerProps` on the trigger element; mouse and keyboard keep Radix's own behaviour.
 */
export function useTapTooltip() {
  const [open, setOpen] = useState(false);
  const tap = useRef({ touch: false, wasOpen: false });

  return {
    rootProps: { open, onOpenChange: setOpen },
    triggerProps: {
      onPointerDown: (event: PointerEvent<HTMLElement>) => {
        // Runs before Radix closes an open tooltip on press, so `open` is still the state the
        // tap started from.
        tap.current = { touch: event.pointerType === "touch", wasOpen: open };
      },
      onClick: (event: MouseEvent<HTMLElement>) => {
        // On touch a tap toggles the tooltip; preventDefault skips Radix's close-on-click.
        if (tap.current.touch) {
          event.preventDefault();
          setOpen(!tap.current.wasOpen);
        }
        tap.current.touch = false;
      },
    },
  };
}
