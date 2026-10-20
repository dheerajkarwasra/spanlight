import { CircleAlert, X } from "lucide-react";
import { useId, useRef, useState, type KeyboardEvent } from "react";

import { Popover, PopoverAnchor, PopoverContent } from "@/components/ui/popover";
import { useFilterOptionsQuery } from "@/features/shell";
import { cn } from "@/lib/utils";

import { PromotedKeySuggestions } from "./promoted-key-suggestions";
import { addKey, keyProblem, MAX_PROMOTED_KEYS } from "./promoted-keys";

interface PromotedKeysInputProps {
  id: string;
  /** The field's label, which also names the chip list. */
  labelId: string;
  keys: readonly string[];
  onChange: (keys: string[]) => void;
  /** A form or server error for the whole list. */
  serverError: string | undefined;
  /** The counter and the note, which the text box points at. */
  describedBy: string;
}

/** Why the typed text can't be added yet, or null when it is a new, valid key (or empty). */
function draftProblem(keys: readonly string[], draft: string): string | null {
  if (draft === "") {
    return null;
  }
  return keys.includes(draft) ? `${draft} is already promoted.` : keyProblem(draft);
}

/**
 * The admin's chip input: a text box checked inline against the key pattern, Enter, a comma
 * or leaving the box adds the key, Backspace in an empty box removes the last chip, and the
 * keys seen in the last 7 days are suggested. At the cap the box is gone until a key is removed.
 */
export function PromotedKeysInput({
  id,
  labelId,
  keys,
  onChange,
  serverError,
  describedBy,
}: PromotedKeysInputProps) {
  const listId = useId();
  const errorId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [text, setText] = useState("");
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const seen = useFilterOptionsQuery().data?.metadata_keys ?? [];
  const draft = text.trim();
  const full = keys.length >= MAX_PROMOTED_KEYS;
  const suggestions = seen.filter((key) => !keys.includes(key) && key.includes(draft));
  const showList = open && !full && suggestions.length > 0;
  const error = draftProblem(keys, draft) ?? serverError;

  function add(key: string) {
    if (draftProblem(keys, key) === null && key !== "") {
      onChange(addKey(keys, key));
      setText("");
      setActiveIndex(-1);
    }
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if ((event.key === "ArrowDown" || event.key === "ArrowUp") && suggestions.length > 0) {
      event.preventDefault();
      setOpen(true);
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActiveIndex((activeIndex + step + suggestions.length) % suggestions.length);
    } else if ((event.key === "Enter" || event.key === ",") && (draft !== "" || showList)) {
      event.preventDefault();
      add(showList && activeIndex >= 0 ? (suggestions[activeIndex] ?? draft) : draft);
    } else if (event.key === "Escape" && showList) {
      event.preventDefault();
      setOpen(false);
    } else if (event.key === "Backspace" && text === "" && keys.length > 0) {
      onChange(keys.slice(0, -1));
    }
  }

  return (
    <Popover open={showList} onOpenChange={setOpen}>
      <PopoverAnchor asChild>
        <div
          onClick={() => inputRef.current?.focus()}
          className={cn(
            "flex min-h-12 flex-wrap items-center gap-1.5 rounded-input border border-input bg-surface px-2.5 py-2",
            "max-md:flex-col max-md:items-stretch max-md:gap-1 max-md:p-1",
            "transition-colors duration-200 ease-out-quart",
            "field-focus-within",
            // A ring, not a 2 px border, so the content doesn't shift.
            error &&
              "border-danger ring-1 ring-danger focus-within:border-danger focus-within:shadow-[0_0_0_3px_var(--color-danger-ring-soft)]",
          )}
        >
          {keys.length > 0 ? (
            <ul
              aria-labelledby={labelId}
              className="flex flex-wrap gap-1.5 max-md:flex-col max-md:gap-1"
            >
              {keys.map((key) => (
                <li
                  key={key}
                  className="flex items-center gap-1.5 rounded-full border border-border bg-surface-muted py-1 pr-1 pl-2.5 max-md:h-11 max-md:justify-between max-md:rounded-[12px] max-md:py-0 max-md:pr-0 max-md:pl-3"
                >
                  <span className="truncate font-mono text-label text-foreground">{key}</span>
                  <button
                    type="button"
                    aria-label={`Remove ${key}`}
                    onClick={(event) => {
                      event.stopPropagation();
                      onChange(keys.filter((candidate) => candidate !== key));
                      // The button is gone; at the cap the text box mounts only on the next render.
                      requestAnimationFrame(() => inputRef.current?.focus());
                    }}
                    className="flex size-6 shrink-0 items-center justify-center rounded-full text-foreground hover:bg-surface-hover focus-visible:outline-[1.5px] focus-visible:outline-ring max-md:size-11"
                  >
                    <X aria-hidden className="size-3" strokeWidth={2.5} />
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
          {full ? null : (
            <input
              ref={inputRef}
              id={id}
              type="text"
              role="combobox"
              aria-expanded={showList}
              aria-controls={showList ? listId : undefined}
              aria-autocomplete="list"
              aria-activedescendant={
                showList && activeIndex >= 0 ? `${listId}-${activeIndex}` : undefined
              }
              aria-invalid={error ? true : undefined}
              aria-describedby={error ? `${errorId} ${describedBy}` : describedBy}
              value={text}
              autoComplete="off"
              spellCheck={false}
              placeholder={keys.length === 0 ? "Add a key, for example customer_id" : undefined}
              onChange={(event) => {
                setText(event.target.value);
                setActiveIndex(-1);
                setOpen(true);
              }}
              onFocus={() => setOpen(true)}
              onBlur={() => add(draft)}
              onKeyDown={handleKeyDown}
              className="h-8 min-w-[8rem] flex-1 bg-transparent px-1 font-mono text-label text-foreground outline-none placeholder:font-sans placeholder:text-sm placeholder:text-subtle-foreground max-md:h-11 max-md:px-2"
            />
          )}
        </div>
      </PopoverAnchor>
      <PopoverContent
        className="w-[276px] p-2"
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
        <PromotedKeySuggestions
          listId={listId}
          keys={suggestions}
          activeIndex={activeIndex}
          onPick={add}
        />
      </PopoverContent>
      {error ? (
        <p id={errorId} className="flex items-start gap-1.5 text-xs font-medium text-danger-text">
          <CircleAlert aria-hidden className="mt-px size-3.5 shrink-0" strokeWidth={2.25} />
          {error}
        </p>
      ) : null}
      {full ? (
        <p className="text-xs font-medium text-muted-foreground">
          All {MAX_PROMOTED_KEYS} slots are used. Remove a key to promote another.
        </p>
      ) : null}
    </Popover>
  );
}
