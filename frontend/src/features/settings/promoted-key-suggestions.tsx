import { cn } from "@/lib/utils";

interface PromotedKeySuggestionsProps {
  /** The listbox id; option `i` is `${listId}-${i}` for the text box's aria-activedescendant. */
  listId: string;
  keys: readonly string[];
  activeIndex: number;
  onPick: (key: string) => void;
}

/** Figma "Suggestions (popover)": the metadata keys seen in the last 7 days, not yet promoted. */
export function PromotedKeySuggestions({
  listId,
  keys,
  activeIndex,
  onPick,
}: PromotedKeySuggestionsProps) {
  return (
    <>
      <p
        id={`${listId}-label`}
        className="px-2.5 pt-1.5 pb-1 text-overline text-muted-foreground uppercase"
      >
        Seen in the last 7 days
      </p>
      <ul
        id={listId}
        role="listbox"
        aria-labelledby={`${listId}-label`}
        className="flex flex-col gap-0.5"
      >
        {keys.map((key, index) => (
          <li
            key={key}
            id={`${listId}-${index}`}
            role="option"
            aria-selected={index === activeIndex}
            // Keep focus in the text box.
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => onPick(key)}
            className={cn(
              "flex h-9 cursor-default items-center rounded-xl px-2.5 font-mono text-label text-foreground hover:bg-surface-hover",
              index === activeIndex && "bg-surface-hover",
            )}
          >
            {key}
          </li>
        ))}
      </ul>
    </>
  );
}
