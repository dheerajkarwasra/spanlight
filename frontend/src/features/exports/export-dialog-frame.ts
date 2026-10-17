/** What both export dialogs share that is not a component: their frame and dismiss rule. */

/** The export dialogs' frame: a centred 560 px dialog from 640 px, a bottom sheet below. */
export const EXPORT_DIALOG_CLASS =
  "max-w-[560px] gap-5 rounded-card p-7 max-sm:top-auto max-sm:bottom-0 max-sm:max-h-[calc(100dvh-1rem)] max-sm:w-full max-sm:max-w-none max-sm:translate-y-0 max-sm:overflow-y-auto max-sm:rounded-b-none max-sm:p-5 max-sm:pb-7";

/** A Radix dismiss handler that cancels the dismissal while `blocked`. */
export function blockWhile(blocked: boolean) {
  return (event: Event) => {
    if (blocked) {
      event.preventDefault();
    }
  };
}
