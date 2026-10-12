import { CircleAlert } from "lucide-react";
import { useId } from "react";

import { Input } from "@/components/ui/input";
import type { ScoreConfig } from "@/lib/api";
import { CATEGORY_LIMIT, configChipText } from "@/lib/score-format";

import { ChoiceButtons, type Choice } from "./choice-buttons";

const BOOLEAN_CHOICES: Choice<string>[] = [
  { value: "true", label: "Yes" },
  { value: "false", label: "No" },
];

interface ScoreValueFieldProps {
  config: ScoreConfig;
  /** Typed text for a number or an open category; `"true"`/`"false"` or the picked category. */
  value: string;
  onChange: (value: string) => void;
  error: string | null;
  disabled: boolean;
}

/**
 * The "Value" of the Add score dialog, shaped by the config: a number input with its range on
 * the right of the label, Yes / No, one option per category, or free text for an open list.
 */
export function ScoreValueField({
  config,
  value,
  onChange,
  error,
  disabled,
}: ScoreValueFieldProps) {
  const id = useId();
  const labelId = `${id}-label`;
  const errorId = `${id}-error`;
  const choices =
    config.data_type === "boolean"
      ? BOOLEAN_CHOICES
      : config.data_type === "categorical" && config.categories.length > 0
        ? config.categories.map((category) => ({ value: category, label: category }))
        : null;
  const describedBy = error ? errorId : undefined;

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-baseline justify-between gap-2">
        <label
          id={labelId}
          htmlFor={choices ? undefined : id}
          className="text-label font-semibold text-foreground"
        >
          Value
        </label>
        <span className="text-2xs font-medium text-muted-foreground">{configChipText(config)}</span>
      </div>
      {choices ? (
        <ChoiceButtons
          choices={choices}
          value={value === "" ? null : value}
          onChange={onChange}
          a11y={{ labelledBy: labelId, describedBy }}
          state={{ disabled, invalid: error !== null }}
        />
      ) : (
        <Input
          id={id}
          value={value}
          disabled={disabled}
          inputMode={config.data_type === "numeric" ? "decimal" : "text"}
          maxLength={config.data_type === "numeric" ? 32 : CATEGORY_LIMIT}
          autoComplete="off"
          placeholder={config.data_type === "numeric" ? "e.g. 0.8" : "Type a value"}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy}
          className={config.data_type === "numeric" ? "font-mono" : undefined}
          onChange={(event) => {
            onChange(event.target.value);
          }}
        />
      )}
      {error ? (
        <p
          id={errorId}
          role="alert"
          className="flex items-start gap-1.5 text-xs font-medium text-danger-text"
        >
          <CircleAlert aria-hidden className="mt-px size-3.5 shrink-0" strokeWidth={2.25} />
          {error}
        </p>
      ) : null}
    </div>
  );
}
