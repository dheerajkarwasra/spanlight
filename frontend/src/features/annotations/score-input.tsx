import { Check, CircleAlert } from "lucide-react";
import { useId } from "react";

import { Input } from "@/components/ui/input";
import type { ScoreConfig } from "@/lib/api";
import { CATEGORY_LIMIT, configChipText, rangeText } from "@/lib/score-format";
import { cn } from "@/lib/utils";

import { KeyChip } from "./key-chip";
import { choicesOf, inputProblem, optionLabel, quickValues } from "./score-draft";

interface ScoreInputProps {
  config: ScoreConfig;
  value: string | null;
  onChange: (value: string | null) => void;
  /** Keys `1`–`9` act on this score right now; its options show their key chips. */
  keyed: boolean;
  onFocusWithin: () => void;
}

/**
 * One score of the work-mode panel (Figma "Annotate — work mode" scores): a number input with
 * quick-fill values, a row of yes/no or category options with their keys, or a free-text box for
 * a category with an open list. Values are checked against the score config as they change.
 */
export function ScoreInput({ config, value, onChange, keyed, onFocusWithin }: ScoreInputProps) {
  const id = useId();
  const choices = choicesOf(config);
  const problem = inputProblem(config, value);
  const isSet = value !== null && value.trim() !== "" && problem === null;

  return (
    <div className="flex flex-col gap-2" onFocus={onFocusWithin}>
      <div className="flex items-center gap-2">
        <span id={`${id}-name`} className="text-sm font-semibold text-foreground">
          {config.name}
        </span>
        <span className="rounded-full border border-border-strong bg-surface px-[9px] py-[3px] text-2xs font-medium text-muted-foreground">
          {configChipText(config)}
        </span>
        <span className="ml-auto flex items-center gap-1 text-xs font-semibold text-muted-foreground">
          {isSet ? <Check aria-hidden className="size-3 text-success" strokeWidth={2.5} /> : null}
          {isSet ? "Set" : "Required"}
        </span>
      </div>
      {choices !== null ? (
        <ChoiceOptions
          config={config}
          choices={choices}
          value={value}
          onChange={onChange}
          keyed={keyed}
          labelledBy={`${id}-name`}
        />
      ) : config.data_type === "numeric" ? (
        <NumericField
          config={config}
          value={value}
          onChange={onChange}
          problem={problem}
          labelledBy={`${id}-name`}
        />
      ) : (
        <Input
          aria-labelledby={`${id}-name`}
          value={value ?? ""}
          maxLength={CATEGORY_LIMIT}
          placeholder="Type a category"
          onChange={(event) => onChange(event.target.value === "" ? null : event.target.value)}
        />
      )}
    </div>
  );
}

interface ChoiceOptionsProps {
  config: ScoreConfig;
  choices: readonly string[];
  value: string | null;
  onChange: (value: string | null) => void;
  keyed: boolean;
  labelledBy: string;
}

function ChoiceOptions({
  config,
  choices,
  value,
  onChange,
  keyed,
  labelledBy,
}: ChoiceOptionsProps) {
  return (
    <div role="group" aria-labelledby={labelledBy} className="flex flex-wrap gap-2">
      {choices.map((option, index) => {
        const picked = value === option;
        const key = index < 9 ? String(index + 1) : null;
        return (
          <button
            key={option}
            type="button"
            aria-pressed={picked}
            aria-keyshortcuts={keyed && key !== null ? key : undefined}
            data-score-option=""
            onClick={() => onChange(option)}
            className={cn(
              "inline-flex h-11 items-center justify-center gap-2 rounded-input px-3 text-sm text-foreground transition-colors",
              picked
                ? "border-2 border-accent bg-surface-selected font-bold"
                : "border border-border-strong bg-surface font-medium hover:bg-surface-hover",
            )}
          >
            {optionLabel(config, option)}
            {picked ? <Check aria-hidden className="size-3.5" strokeWidth={2.5} /> : null}
            {key !== null ? <KeyChip className={cn(!keyed && "opacity-60")}>{key}</KeyChip> : null}
          </button>
        );
      })}
    </div>
  );
}

interface NumericFieldProps {
  config: ScoreConfig;
  value: string | null;
  onChange: (value: string | null) => void;
  problem: string | null;
  labelledBy: string;
}

function NumericField({ config, value, onChange, problem, labelledBy }: NumericFieldProps) {
  const id = useId();
  // The bounds as a hint inside the field; a number without bounds needs none.
  const range = config.min_value !== null || config.max_value !== null ? rangeText(config) : null;
  const presets = quickValues(config);
  return (
    <>
      <div className="relative">
        <Input
          aria-labelledby={labelledBy}
          aria-invalid={problem !== null || undefined}
          aria-describedby={problem === null ? undefined : `${id}-error`}
          data-score-input="numeric"
          inputMode="decimal"
          autoComplete="off"
          value={value ?? ""}
          onChange={(event) => onChange(event.target.value === "" ? null : event.target.value)}
          className="pr-20 font-mono text-lg font-bold"
        />
        {range === null ? null : (
          <span
            aria-hidden
            className="pointer-events-none absolute top-1/2 right-3.5 -translate-y-1/2 text-xs font-medium text-muted-foreground"
          >
            {range}
          </span>
        )}
      </div>
      {problem === null ? null : (
        <p
          id={`${id}-error`}
          className="flex items-start gap-1.5 text-xs font-medium text-danger-text"
        >
          <CircleAlert aria-hidden className="mt-px size-3.5 shrink-0" strokeWidth={2.25} />
          {problem}
        </p>
      )}
      {presets.length === 0 ? null : (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-xs font-medium text-muted-foreground">Quick fill</span>
          {presets.map((preset) => (
            <button
              key={preset}
              type="button"
              data-score-option=""
              aria-label={`Set ${config.name} to ${preset}`}
              onClick={() => onChange(preset)}
              className="h-8 rounded-full border border-border-strong bg-surface px-3 font-mono text-xs font-medium text-foreground hover:bg-surface-hover"
            >
              {preset}
            </button>
          ))}
        </div>
      )}
    </>
  );
}
