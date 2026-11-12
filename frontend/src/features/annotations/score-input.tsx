import { Check, CircleAlert } from "lucide-react";
import { Fragment, useId } from "react";

import { Input } from "@/components/ui/input";
import type { ScoreConfig } from "@/lib/api";
import { ScoreConfigChip } from "@/features/scores";
import { CATEGORY_LIMIT, rangeText } from "@/lib/score-format";
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
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span
          id={`${id}-name`}
          className="min-w-0 text-sm font-semibold [overflow-wrap:anywhere] text-foreground"
        >
          <Breakable text={config.name} />
        </span>
        <ScoreConfigChip config={config} />
        <span className="ml-auto flex shrink-0 items-center gap-1 text-xs font-semibold text-muted-foreground">
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

/** A long snake_case name wraps after a separator instead of mid-word. */
function Breakable({ text }: { text: string }) {
  return text.split(/(?<=[_\-./])/).map((part, index) => (
    <Fragment key={index}>
      {part}
      <wbr />
    </Fragment>
  ));
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
    <div role="group" aria-labelledby={labelledBy} className="grid grid-cols-2 gap-2">
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
              // One weight in both states: picking an option never reflows its name.
              "flex min-h-11 min-w-0 items-center gap-1.5 rounded-input py-1.5 text-left text-sm font-medium text-foreground transition-colors",
              picked
                ? "border-2 border-accent bg-surface-selected px-[9px]"
                : "border border-border-strong bg-surface px-2.5 hover:bg-surface-hover",
            )}
          >
            <span className="min-w-0 flex-1 [overflow-wrap:anywhere]">
              <Breakable text={optionLabel(config, option)} />
            </span>
            {/* The picked mark: the filled key chip, or a check where there is no chip. */}
            {picked ? (
              <Check
                aria-hidden
                className={cn("size-3.5 shrink-0", key !== null && "sm:hidden")}
                strokeWidth={2.5}
              />
            ) : null}
            {key !== null ? (
              <KeyChip
                className={cn(
                  "hidden sm:inline-flex",
                  picked && "border-accent bg-accent text-accent-foreground",
                  !keyed && "opacity-60",
                )}
              >
                {key}
              </KeyChip>
            ) : null}
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
  // A long range ("-1000000 to 1000000") would sit on top of the typed value: it goes below.
  const inlineRange = range !== null && range.length <= 12;
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
          className={cn("font-mono text-lg font-bold", inlineRange && "pr-20")}
        />
        {!inlineRange ? null : (
          <span
            aria-hidden
            className="pointer-events-none absolute top-1/2 right-3.5 -translate-y-1/2 text-xs font-medium text-muted-foreground"
          >
            {range}
          </span>
        )}
      </div>
      {range === null || inlineRange ? null : (
        <p aria-hidden className="text-xs font-medium text-muted-foreground">
          {range}
        </p>
      )}
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
