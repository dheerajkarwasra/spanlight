import { X } from "lucide-react";
import { useEffect, useState, type ClipboardEvent, type KeyboardEvent } from "react";

import { cn } from "@/lib/utils";

import { parseList } from "./key-form";

interface ChipInputProps {
  value: readonly string[];
  onChange: (value: string[]) => void;
  placeholder: string;
  /** Why an item may not be added, or null. Run on every item before it joins the list. */
  validate: (item: string, current: readonly string[]) => string | null;
  /** Wired by `FormField`: the label's target and the hint/error description. */
  id?: string;
  "aria-invalid"?: boolean;
  "aria-describedby"?: string;
  disabled?: boolean;
  /**
   * What the text box holds that is not in the list: the reason a typed value was refused, or a
   * reminder to press Enter. Null when the box is empty.
   */
  onDraftProblem?: (message: string | null) => void;
}

/**
 * A list of short text values in one field (Figma "Allowed models", "Default tags"). Enter, a
 * comma or leaving the field adds the typed text; a pasted comma list adds every item; Backspace
 * on an empty field takes the last one back. A value that fails `validate` stays in the field with
 * the reason under it.
 */
export function ChipInput({
  value,
  onChange,
  placeholder,
  validate,
  id,
  "aria-invalid": ariaInvalid,
  "aria-describedby": ariaDescribedBy,
  disabled,
  onDraftProblem,
}: ChipInputProps) {
  const [draft, setDraft] = useState("");
  const [rejection, setRejection] = useState<string | null>(null);
  const rejectionId = id ? `${id}-rejection` : undefined;

  useEffect(() => {
    const pending = draft.trim();
    onDraftProblem?.(
      rejection ?? (pending === "" ? null : `Press Enter to add "${pending}", or clear it.`),
    );
  }, [draft, rejection, onDraftProblem]);

  /** Adds each item in turn; stops at the first one that fails and leaves it in the field. */
  function commit(text: string) {
    const items = parseList(text);
    const next = [...value];
    for (const item of items) {
      if (next.includes(item)) {
        continue;
      }
      const problem = validate(item, next);
      if (problem) {
        onChange(next);
        setDraft(items.slice(items.indexOf(item)).join(", "));
        setRejection(problem);
        return;
      }
      next.push(item);
    }
    onChange(next);
    setDraft("");
    setRejection(null);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter" || event.key === ",") {
      event.preventDefault();
      commit(draft);
    } else if (event.key === "Backspace" && draft === "" && value.length > 0) {
      onChange(value.slice(0, -1));
    }
  }

  function handlePaste(event: ClipboardEvent<HTMLInputElement>) {
    const text = event.clipboardData.getData("text");
    if (text.includes(",")) {
      event.preventDefault();
      commit(`${draft}${text}`);
    }
  }

  return (
    <div className="flex flex-col gap-1.5">
      <div
        className={cn(
          "flex min-h-[46px] flex-wrap items-center gap-1.5 rounded-input border border-input bg-surface px-3 py-1.5",
          "transition-colors duration-200 ease-out-quart",
          "field-focus-within",
          (ariaInvalid || rejection) && "border-[1.5px] border-danger",
          disabled && "cursor-not-allowed bg-surface-muted opacity-60",
        )}
      >
        {value.map((item) => (
          <span
            key={item}
            className="inline-flex max-w-full items-center gap-1 rounded-full border border-border bg-surface py-0.5 pr-1 pl-2.5 font-mono text-label text-foreground"
          >
            <span className="truncate">{item}</span>
            <button
              type="button"
              disabled={disabled}
              aria-label={`Remove ${item}`}
              onClick={() => {
                onChange(value.filter((candidate) => candidate !== item));
              }}
              className="flex size-4 shrink-0 items-center justify-center rounded-full text-muted-foreground hover:bg-surface-hover hover:text-foreground"
            >
              <X aria-hidden className="size-3" />
            </button>
          </span>
        ))}
        <input
          id={id}
          type="text"
          value={draft}
          disabled={disabled}
          autoComplete="off"
          spellCheck={false}
          placeholder={value.length === 0 ? placeholder : "Add another"}
          aria-invalid={ariaInvalid || rejection ? true : undefined}
          aria-describedby={
            [ariaDescribedBy, rejection ? rejectionId : null].filter(Boolean).join(" ") || undefined
          }
          onChange={(event) => {
            setDraft(event.target.value);
            setRejection(null);
          }}
          onKeyDown={handleKeyDown}
          onPaste={handlePaste}
          onBlur={() => {
            if (draft.trim() !== "") {
              commit(draft);
            }
          }}
          className="h-8 min-w-[8rem] flex-1 bg-transparent text-sm text-foreground outline-none placeholder:text-subtle-foreground"
        />
      </div>
      {rejection ? (
        <p id={rejectionId} role="alert" className="text-xs font-medium text-danger-text">
          {rejection}
        </p>
      ) : null}
    </div>
  );
}
