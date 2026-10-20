import { Check } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

import { optionId, type ChipOption } from "@/components/chip-option";

interface ChipOptionListProps {
  listId: string;
  /** The listbox's accessible name, e.g. "Recipients". */
  label: string;
  options: readonly ChipOption[];
  selected: readonly string[];
  /** Index of the keyboard-highlighted option, mirrored by the input's aria-activedescendant. */
  activeIndex: number;
  /** Toggles one option; `full` options (limit reached) arrive here only to be removed. */
  onToggle: (option: ChipOption) => void;
}

/**
 * Figma "Alerts/Member option": the picker's list, a checkbox per row. Focus stays in the text
 * box; the highlighted row is announced through aria-activedescendant.
 */
export function ChipOptionList({
  listId,
  label,
  options,
  selected,
  activeIndex,
  onToggle,
}: ChipOptionListProps) {
  return (
    <>
      {options.length === 0 ? (
        <p className="px-3 py-2.5 text-sm text-muted-foreground">No matches.</p>
      ) : null}
      <ul
        id={listId}
        role="listbox"
        aria-label={label}
        aria-multiselectable
        className="flex flex-col gap-0.5 empty:hidden"
      >
        {options.map((option, index) => {
          const checked = selected.includes(option.value);
          const disabled = option.disabledNote !== undefined && !checked;
          return (
            <li
              key={option.value}
              id={optionId(listId, index)}
              role="option"
              aria-selected={checked}
              aria-disabled={disabled || undefined}
              onMouseDown={(event) => {
                // Keep focus in the text box.
                event.preventDefault();
              }}
              onClick={() => {
                if (!disabled) {
                  onToggle(option);
                }
              }}
              className={cn(
                "flex cursor-default items-center gap-3 rounded-tile px-3 py-2 text-sm",
                checked && "bg-accent-subtle",
                index === activeIndex && !checked && "bg-surface-muted",
                index === activeIndex && "outline-[1.5px] outline-offset-[-2px] outline-ring",
                disabled && "opacity-60",
              )}
            >
              {option.leading}
              <span className="flex min-w-0 flex-1 flex-col">
                <span className="truncate font-semibold text-foreground">{option.label}</span>
                {option.detail ? (
                  <span className="truncate text-xs text-muted-foreground">{option.detail}</span>
                ) : null}
              </span>
              {disabled ? (
                <Badge size="sm">{option.disabledNote}</Badge>
              ) : (
                <span
                  aria-hidden
                  className={cn(
                    "flex size-4 shrink-0 items-center justify-center rounded-checkbox border",
                    checked ? "border-accent bg-accent text-accent-foreground" : "border-input",
                  )}
                >
                  {checked ? <Check className="size-3" strokeWidth={3} /> : null}
                </span>
              )}
            </li>
          );
        })}
      </ul>
    </>
  );
}
