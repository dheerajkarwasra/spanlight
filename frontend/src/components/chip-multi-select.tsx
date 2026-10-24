import { X } from "lucide-react";
import { useId, useRef, useState, type KeyboardEvent } from "react";

import { optionId, type ChipOption } from "@/components/chip-option";
import { ChipOptionList } from "@/components/chip-option-list";
import { Popover, PopoverAnchor, PopoverContent } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

export type { ChipOption } from "@/components/chip-option";

interface ChipMultiSelectProps {
  value: readonly string[];
  onChange: (value: string[]) => void;
  /** Everything that can be picked, plus any picked value the list doesn't know (shown as is). */
  options: readonly ChipOption[];
  placeholder: string;
  /** Names the list of choices for assistive technology, e.g. "Recipients". */
  label?: string;
  /** At most this many picks; the rest of the list can't be added once it is reached. */
  max: number;
  /** Wired by `FormField`. */
  id?: string;
  "aria-invalid"?: boolean;
  "aria-describedby"?: string;
}

function matches(option: ChipOption, query: string): boolean {
  const needle = query.trim().toLowerCase();
  return (
    needle === "" ||
    [option.label, option.detail ?? "", option.value].some((text) =>
      text.toLowerCase().includes(needle),
    )
  );
}

/**
 * Figma "Recipients" and "Channels" fields: picked values as chips (Figma "Alerts/Channel chip",
 * error state for a value the server refused) and a text box that filters a list to pick from.
 * Arrow keys move through the list, Enter toggles, Backspace in an empty box removes the last
 * chip, Escape closes the list.
 */
export function ChipMultiSelect({
  value,
  onChange,
  options,
  placeholder,
  label = "Choices",
  max,
  id,
  "aria-invalid": ariaInvalid,
  "aria-describedby": ariaDescribedBy,
}: ChipMultiSelectProps) {
  const listId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const full = value.length >= max;
  // Once the limit is reached, the rest of the list stays visible but can't be picked.
  const visible = options
    .filter((option) => matches(option, query))
    .map((option) =>
      full && !value.includes(option.value) && option.disabledNote === undefined
        ? { ...option, disabledNote: `Limit of ${max} reached` }
        : option,
    );
  const active = Math.min(activeIndex, Math.max(visible.length - 1, 0));
  const byValue = new Map(options.map((option) => [option.value, option]));

  function toggle(option: ChipOption) {
    if (value.includes(option.value)) {
      onChange(value.filter((picked) => picked !== option.value));
    } else if (!full) {
      onChange([...value, option.value]);
      setQuery("");
    }
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      setOpen(true);
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActiveIndex((active + step + visible.length) % Math.max(visible.length, 1));
    } else if (event.key === "Enter") {
      event.preventDefault();
      const option = visible[active];
      if (open && option && (option.disabledNote === undefined || value.includes(option.value))) {
        toggle(option);
      }
    } else if (event.key === "Escape" && open) {
      event.preventDefault();
      setOpen(false);
    } else if (event.key === "Backspace" && query === "" && value.length > 0) {
      onChange(value.slice(0, -1));
    }
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverAnchor asChild>
        <div
          onClick={() => inputRef.current?.focus()}
          className={cn(
            "flex min-h-[46px] flex-wrap items-center gap-1.5 rounded-input border border-input bg-surface px-3 py-1.5",
            "transition-colors duration-200 ease-out-quart",
            "field-focus-within",
            ariaInvalid &&
              "border-[1.5px] border-danger focus-within:border-danger focus-within:shadow-[0_0_0_3px_var(--color-danger-ring-soft)]",
          )}
        >
          {value.map((picked) => {
            const option = byValue.get(picked);
            const text = option?.chip ?? option?.label ?? picked;
            return (
              <span
                key={picked}
                className={cn(
                  "inline-flex max-w-full items-center gap-1 rounded-full border py-0.5 pr-1 pl-2.5 text-xs font-medium",
                  option?.invalid
                    ? "border-danger bg-danger-subtle text-danger-text"
                    : "border-border bg-surface text-foreground",
                )}
              >
                <span className="truncate">{text}</span>
                <button
                  type="button"
                  aria-label={`Remove ${text}`}
                  onClick={(event) => {
                    event.stopPropagation();
                    onChange(value.filter((candidate) => candidate !== picked));
                  }}
                  className="-my-1 flex size-6 shrink-0 items-center justify-center rounded-full hover:bg-surface-hover"
                >
                  <X aria-hidden className="size-3" strokeWidth={2.5} />
                </button>
              </span>
            );
          })}
          <input
            ref={inputRef}
            id={id}
            type="text"
            role="combobox"
            aria-expanded={open}
            aria-controls={open ? listId : undefined}
            aria-autocomplete="list"
            aria-activedescendant={
              open && visible.length > 0 ? optionId(listId, active) : undefined
            }
            aria-invalid={ariaInvalid}
            aria-describedby={ariaDescribedBy}
            value={query}
            autoComplete="off"
            spellCheck={false}
            placeholder={value.length === 0 || !full ? placeholder : undefined}
            onChange={(event) => {
              setQuery(event.target.value);
              setActiveIndex(0);
              setOpen(true);
            }}
            onFocus={() => setOpen(true)}
            onKeyDown={handleKeyDown}
            className="h-8 min-w-[8rem] flex-1 bg-transparent text-sm text-foreground outline-none placeholder:text-subtle-foreground"
          />
        </div>
      </PopoverAnchor>
      <PopoverContent
        className="max-h-72 w-(--radix-popover-trigger-width) overflow-y-auto p-1.5"
        onOpenAutoFocus={(event) => event.preventDefault()}
        onCloseAutoFocus={(event) => event.preventDefault()}
        onInteractOutside={(event) => {
          // A click on the field itself keeps the list open.
          if (
            event.target instanceof Node &&
            inputRef.current?.parentElement?.contains(event.target)
          ) {
            event.preventDefault();
          }
        }}
      >
        <ChipOptionList
          listId={listId}
          options={visible}
          selected={value}
          label={label}
          activeIndex={active}
          onToggle={toggle}
        />
      </PopoverContent>
    </Popover>
  );
}
