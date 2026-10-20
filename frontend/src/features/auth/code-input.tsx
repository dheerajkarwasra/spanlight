import { Fragment, useState, type Ref } from "react";

import { cn } from "@/lib/utils";

import { CODE_LENGTH, digitsOnly } from "./two-factor-flow";

export type CodeInputSize = "md" | "lg";

interface CodeInputProps {
  value: string;
  onChange: (value: string) => void;
  /** The code was refused: the cells turn red until the next edit. */
  invalid: boolean;
  /**
   * Cell size from `sm` up (Figma "Control/Code digit"): `lg` is 56 x 64, as on the sign-in step
   * and the default; `md` is 48 x 56, as in the setup wizard, and the cells sit together at the
   * start instead of spreading across the row.
   */
  size?: CodeInputSize;
  /**
   * The one real input: its ref, the visible instruction that labels it, and the error line that
   * describes it.
   */
  input: {
    ref: Ref<HTMLInputElement>;
    "aria-labelledby"?: string | undefined;
    "aria-describedby"?: string | undefined;
  };
}

const CELL_SIZES: Record<CodeInputSize, string> = {
  lg: "sm:h-16 sm:w-14",
  md: "sm:h-14 sm:w-12",
};

type CellState = "idle" | "active" | "error" | "error-active";

const CELL_STATES: Record<CellState, string> = {
  idle: "border border-input",
  active: "border border-ring shadow-[0_0_0_3px_var(--color-ring-soft)]",
  error: "border-[1.5px] border-danger",
  // Refused and focused: the red border stays, and the cell being typed into gets the danger halo.
  "error-active": "border-[1.5px] border-danger shadow-[0_0_0_3px_var(--color-danger-ring-soft)]",
};

function Cell({
  digit,
  state,
  size,
}: {
  digit: string | undefined;
  state: CellState;
  size: CodeInputSize;
}) {
  return (
    <span
      aria-hidden
      className={cn(
        "flex h-14 min-w-0 flex-1 items-center justify-center rounded-input bg-surface text-h2 text-foreground tabular sm:flex-none",
        CELL_SIZES[size],
        CELL_STATES[state],
      )}
    >
      {digit ??
        (state === "active" || state === "error-active" ? (
          <span className="h-6 w-0.5 bg-accent" />
        ) : null)}
    </span>
  );
}

/**
 * The six-digit code: ONE real input, so paste, autofill from a text message and password managers
 * all work, drawn as six cells in two groups of three. The cells are only a picture of its value.
 * Typed or pasted text is reduced to digits.
 */
export function CodeInput({ value, onChange, invalid, size = "lg", input }: CodeInputProps) {
  const [focused, setFocused] = useState(false);
  const activeIndex = Math.min(value.length, CODE_LENGTH - 1);

  function cellState(index: number): CellState {
    const isActive = focused && index === activeIndex;
    if (invalid) {
      return isActive ? "error-active" : "error";
    }
    return isActive ? "active" : "idle";
  }

  return (
    <div
      className={cn(
        "relative flex items-center gap-2",
        size === "lg" ? "justify-between" : "sm:w-fit",
      )}
    >
      {Array.from({ length: CODE_LENGTH }, (_, index) => (
        <Fragment key={index}>
          {index === CODE_LENGTH / 2 ? (
            <span aria-hidden className="h-0.5 w-3 shrink-0 rounded-full bg-border-strong" />
          ) : null}
          <Cell digit={value[index]} state={cellState(index)} size={size} />
        </Fragment>
      ))}
      <input
        {...input}
        value={value}
        onChange={(event) => {
          onChange(digitsOnly(event.target.value));
        }}
        onPaste={(event) => {
          // maxlength would cut "123 456" at six characters, spaces included, before we saw it.
          event.preventDefault();
          onChange(digitsOnly(event.clipboardData.getData("text")));
        }}
        onFocus={() => {
          setFocused(true);
        }}
        onBlur={() => {
          setFocused(false);
        }}
        inputMode="numeric"
        autoComplete="one-time-code"
        maxLength={CODE_LENGTH}
        pattern="[0-9]*"
        spellCheck={false}
        aria-invalid={invalid || undefined}
        className="absolute inset-0 size-full cursor-text opacity-0 outline-none"
      />
    </div>
  );
}
