/**
 * The work-mode keymap, as a pure function of the key and where focus is.
 *
 * - `1`–`9` pick that option of a category or yes/no score.
 * - Digits type into a focused number input (the browser does that; the keymap stays out of it).
 * - `Enter` submits, `S` skips, `N` claims the next item, `?` shows or hides the help.
 * - `Esc` closes the help, then exits review.
 * - Every key is ignored while typing in the comment field (or another free-text field).
 */

/** Where the key was pressed. */
export type KeyTarget =
  /** The comment, or a free-text category: typing there never triggers a shortcut. */
  | "text"
  /** A number score input: digits type, letters and Enter stay shortcuts. */
  | "numeric"
  /** An action button or a link: Enter and Space keep their native meaning. */
  | "control"
  /** Anywhere else on the page. */
  | "page";

export interface KeyInput {
  key: string;
  metaKey: boolean;
  ctrlKey: boolean;
  altKey: boolean;
}

export type ShortcutAction =
  | { type: "pick"; index: number }
  | { type: "submit" }
  | { type: "skip" }
  | { type: "next" }
  | { type: "toggle_help" }
  | { type: "escape" };

/** Every row of the help dialog, in the order it lists them. */
export const SHORTCUT_ROWS: readonly { keys: readonly string[]; range: boolean; label: string }[] =
  [
    { keys: ["1", "9"], range: true, label: "Pick that option for a category or yes/no score" },
    { keys: ["0", "9"], range: true, label: "Type a value when a number score has focus" },
    { keys: ["↵"], range: false, label: "Submit and open the next item" },
    { keys: ["S"], range: false, label: "Skip this item" },
    { keys: ["N"], range: false, label: "Claim the next item" },
    { keys: ["?"], range: false, label: "Show or hide this list" },
    { keys: ["Esc"], range: false, label: "Close this list, then exit review" },
  ];

const LETTERS: Record<string, ShortcutAction> = {
  s: { type: "skip" },
  n: { type: "next" },
};

/**
 * The action for a key press, or null when the key should keep its normal meaning. While the
 * help dialog is open only `?` acts here; the dialog closes itself on `Esc`.
 */
export function shortcutFor(
  input: KeyInput,
  target: KeyTarget,
  helpOpen: boolean,
): ShortcutAction | null {
  if (input.metaKey || input.ctrlKey || input.altKey || target === "text") {
    return null;
  }
  if (input.key === "?") {
    return { type: "toggle_help" };
  }
  if (helpOpen) {
    return null;
  }
  if (input.key === "Escape") {
    return { type: "escape" };
  }
  if (input.key === "Enter") {
    return target === "control" ? null : { type: "submit" };
  }
  const letter = LETTERS[input.key.toLowerCase()];
  if (letter !== undefined) {
    return letter;
  }
  if (target !== "numeric" && /^[1-9]$/.test(input.key)) {
    return { type: "pick", index: Number(input.key) - 1 };
  }
  return null;
}

/** Classifies the focused element for `shortcutFor`. */
export function keyTargetOf(element: EventTarget | null): KeyTarget {
  if (!(element instanceof HTMLElement)) {
    return "page";
  }
  if (element.dataset.scoreInput === "numeric") {
    return "numeric";
  }
  if (
    element.isContentEditable ||
    element.tagName === "TEXTAREA" ||
    element.tagName === "INPUT" ||
    element.tagName === "SELECT"
  ) {
    return "text";
  }
  // Score options and quick-fill values set a value on click; Enter on them still submits.
  if (element.dataset.scoreOption !== undefined) {
    return "page";
  }
  if (element.tagName === "BUTTON" || element.tagName === "A") {
    return "control";
  }
  return "page";
}
