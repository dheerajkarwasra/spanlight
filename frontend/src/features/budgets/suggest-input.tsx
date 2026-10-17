import { ChevronDown } from "lucide-react";
import { useId, useRef, useState, type ComponentProps, type KeyboardEvent } from "react";

import { optionId } from "@/components/chip-option";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Popover, PopoverAnchor, PopoverContent } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

export interface Suggestion {
  value: string;
  /** A pill after the value, in the list and in the field, e.g. "promoted". */
  badge?: string;
}

interface SuggestInputProps extends Omit<
  ComponentProps<"input">,
  "value" | "onChange" | "role" | "className"
> {
  value: string;
  onChange: (value: string) => void;
  suggestions: readonly Suggestion[];
  /** The list's overline, e.g. "Seen in the last 7 days". */
  heading: string;
  /** Draws the field as a select (a chevron that opens the full list). */
  chevron?: boolean;
}

/**
 * Figma "Budgets / Scope picker": a free-text field with a list of known values under it. Typing
 * filters the list; focus or the chevron shows all of it. Selection is manual: no row is active
 * until an arrow key moves to one, so Enter keeps the typed text (the scope is an exact match);
 * Enter on an active row picks it, Escape closes. Focus stays in the field and the active row is
 * its aria-activedescendant.
 */
export function SuggestInput({
  value,
  onChange,
  suggestions,
  heading,
  chevron = false,
  onBlur,
  ...inputProps
}: SuggestInputProps) {
  const listId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [filtering, setFiltering] = useState(false);
  // -1: no active row, so Enter keeps what was typed.
  const [activeIndex, setActiveIndex] = useState(-1);
  const needle = filtering ? value.trim().toLowerCase() : "";
  const visible = suggestions.filter((option) => option.value.toLowerCase().includes(needle));
  const active = Math.min(activeIndex, visible.length - 1);
  const shown = open && visible.length > 0;
  const badge = suggestions.find((option) => option.value === value)?.badge;

  function show(filter: boolean) {
    setFiltering(filter);
    setActiveIndex(-1);
    setOpen(true);
  }

  function pick(next: string) {
    onChange(next);
    setOpen(false);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!open) {
        show(false);
        return;
      }
      const count = Math.max(visible.length, 1);
      const down = event.key === "ArrowDown";
      setActiveIndex(
        active < 0 ? (down ? 0 : count - 1) : (active + (down ? 1 : -1) + count) % count,
      );
    } else if (event.key === "Enter" && shown && active >= 0) {
      // Pick the row instead of submitting the form.
      event.preventDefault();
      const option = visible[active];
      if (option) {
        pick(option.value);
      }
    } else if (event.key === "Enter") {
      setOpen(false);
    } else if (event.key === "Escape" && shown) {
      event.preventDefault();
      event.stopPropagation();
      setOpen(false);
    }
  }

  return (
    <Popover open={shown} onOpenChange={setOpen}>
      <PopoverAnchor asChild>
        <div className="relative">
          <Input
            ref={inputRef}
            role="combobox"
            aria-expanded={shown}
            aria-controls={shown ? listId : undefined}
            aria-autocomplete="list"
            aria-activedescendant={shown && active >= 0 ? optionId(listId, active) : undefined}
            autoComplete="off"
            spellCheck={false}
            value={value}
            onChange={(event) => {
              onChange(event.target.value);
              show(true);
            }}
            onFocus={() => show(false)}
            onClick={() => {
              if (!open) {
                show(false);
              }
            }}
            onBlur={onBlur}
            onKeyDown={handleKeyDown}
            className={cn("font-mono text-[13px]", badge && "pr-32", chevron && !badge && "pr-10")}
            {...inputProps}
          />
          <span className="pointer-events-none absolute inset-y-0 right-3.5 flex items-center gap-2">
            {badge ? <Badge variant="accent">{badge}</Badge> : null}
            {chevron ? <ChevronDown aria-hidden className="size-4 text-muted-foreground" /> : null}
          </span>
        </div>
      </PopoverAnchor>
      <PopoverContent
        align="start"
        sideOffset={4}
        className="max-h-72 w-[max(15rem,min(20rem,var(--radix-popover-trigger-width)))] overflow-y-auto p-1.5"
        onOpenAutoFocus={(event) => event.preventDefault()}
        onCloseAutoFocus={(event) => event.preventDefault()}
        onInteractOutside={(event) => {
          // A click in the field itself keeps the list open.
          if (event.target === inputRef.current) {
            event.preventDefault();
          }
        }}
      >
        <p
          aria-hidden
          className="px-2.5 pt-1.5 pb-1 text-overline text-subtle-foreground uppercase"
        >
          {heading}
        </p>
        <ul id={listId} role="listbox" aria-label={heading} className="flex flex-col gap-0.5">
          {visible.map((option, index) => (
            <li
              key={option.value}
              id={optionId(listId, index)}
              role="option"
              aria-selected={option.value === value}
              onMouseDown={(event) => {
                // Keep focus in the field.
                event.preventDefault();
              }}
              onClick={() => {
                pick(option.value);
              }}
              className={cn(
                "flex min-h-10 cursor-default items-center gap-2 rounded-tile px-2.5 py-2 font-mono text-[13px] text-foreground",
                index === active && "bg-surface-muted",
              )}
            >
              <span className="min-w-0 flex-1 truncate">{option.value}</span>
              {option.badge ? (
                <Badge variant="accent" size="sm" className="font-sans">
                  {option.badge}
                </Badge>
              ) : null}
            </li>
          ))}
        </ul>
      </PopoverContent>
    </Popover>
  );
}
