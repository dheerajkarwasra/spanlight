import { ChevronDown, Info } from "lucide-react";
import { useId, useState, type SubmitEvent } from "react";

import { FieldSelect } from "@/components/field-select";
import { FormField } from "@/components/form-field";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import type { ScoreConfig } from "@/lib/api";
import { cn } from "@/lib/utils";

import { ChoiceButtons, type Choice } from "./choice-buttons";
import {
  COMPARATOR_LABELS,
  COMPARATOR_SYMBOLS,
  scoreFilterDraft,
  toScoreFilterParams,
  type ScoreComparator,
  type ScoreFilterDraft,
  type ScoreFilterParams,
} from "./score-filter";
import { useScoreConfigsQuery, useScoreTypeOf } from "./scores-queries";

const COMPARATORS: Choice<ScoreComparator>[] = (["gte", "lte", "eq"] as const).map((value) => ({
  value,
  label: COMPARATOR_SYMBOLS[value],
  ariaLabel: COMPARATOR_LABELS[value],
}));

interface ScoreFacetProps {
  params: ScoreFilterParams;
  /** Score names seen in the last 30 days; the project's configs are added to them. */
  recentNames: readonly string[];
  onApply: (params: ScoreFilterParams) => void;
  /** The look of the toolbar's facet chips, so this one matches them. */
  chipClassName: string;
}

/**
 * Figma "Traces — explorer with prompt and score filters": the "Score: relevance" chip opens a
 * small form (name, comparator, value) that filters on the latest score of that name per trace.
 */
export function ScoreFacet({ params, recentNames, onApply, chipClassName }: ScoreFacetProps) {
  const [open, setOpen] = useState(false);
  const typeOf = useScoreTypeOf();
  const configs = useScoreConfigsQuery().data;
  const names = Array.from(
    new Set([...recentNames, ...(configs ?? []).map((config) => config.name)]),
  ).sort();

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          className={cn(chipClassName, params.score !== undefined && "border-accent/60")}
        >
          <span className="flex min-w-0 items-center gap-1">
            <span className="shrink-0">Score:</span>
            <span className="min-w-0 truncate">{params.score ?? "Any"}</span>
          </span>
          <ChevronDown aria-hidden />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-80">
        {open ? (
          <ScoreFilterForm
            initial={scoreFilterDraft(params, params.score ? (typeOf(params.score) ?? null) : null)}
            names={names}
            configs={configs}
            onApply={(next) => {
              onApply(next);
              setOpen(false);
            }}
          />
        ) : null}
      </PopoverContent>
    </Popover>
  );
}

interface ScoreFilterFormProps {
  initial: ScoreFilterDraft;
  names: readonly string[];
  configs: readonly ScoreConfig[] | undefined;
  onApply: (params: ScoreFilterParams) => void;
}

function ScoreFilterForm({ initial, names, configs, onApply }: ScoreFilterFormProps) {
  const [draft, setDraft] = useState(initial);
  const config = configs?.find((candidate) => candidate.name === draft.name) ?? null;
  const dataType = config?.data_type ?? null;
  const options = names.includes(draft.name) || !draft.name ? names : [draft.name, ...names];

  function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    onApply(toScoreFilterParams(draft, dataType));
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-3.5">
      <p className="text-sm font-semibold text-foreground">Score filter</p>
      <FormField label="Score name">
        <FieldSelect
          value={draft.name}
          placeholder={options.length === 0 ? "No scores yet" : "Pick a score"}
          options={options.map((name) => ({ value: name, label: name }))}
          onChange={(name) => {
            setDraft({ name, comparator: "gte", value: "" });
          }}
        />
      </FormField>
      <ValueControls config={config} draft={draft} onChange={setDraft} />
      <p className="flex items-start gap-1.5 text-xs font-medium text-muted-foreground">
        <Info aria-hidden className="mt-px size-3.5 shrink-0" />
        {draft.name
          ? `Matches the latest ${draft.name} score on each trace.`
          : "Pick a score to filter on its latest value per trace."}
      </p>
      <div className="flex justify-end gap-2">
        <Button
          size="sm"
          onClick={() => {
            onApply(toScoreFilterParams({ name: "", comparator: "gte", value: "" }, null));
          }}
        >
          Clear
        </Button>
        <Button type="submit" variant="primary" size="sm" disabled={!draft.name}>
          Apply
        </Button>
      </div>
    </form>
  );
}

/** Yes/No, or a categorical config's values; null for a number, an open list or no config. */
function valueChoices(config: ScoreConfig | null): Choice<string>[] | null {
  if (config?.data_type === "boolean") {
    return [
      { value: "true", label: "Yes" },
      { value: "false", label: "No" },
    ];
  }
  if (config?.data_type === "categorical" && config.categories.length > 0) {
    return config.categories.map((category) => ({ value: category, label: category }));
  }
  return null;
}

interface ValueControlsProps {
  config: ScoreConfig | null;
  draft: ScoreFilterDraft;
  onChange: (draft: ScoreFilterDraft) => void;
}

/**
 * A number gets a comparator and a value; a yes/no or listed category gets its options; an open
 * category gets a text box matched exactly.
 */
function ValueControls({ config, draft, onChange }: ValueControlsProps) {
  const id = useId();
  const choices = valueChoices(config);
  if (choices !== null) {
    return (
      <div className="flex flex-col gap-2">
        <span id={`${id}-value`} className="text-label font-semibold text-foreground">
          Value
        </span>
        <ChoiceButtons
          choices={choices}
          value={draft.value === "" ? null : draft.value}
          a11y={{ labelledBy: `${id}-value` }}
          onChange={(value) => {
            onChange({ ...draft, comparator: "eq", value: draft.value === value ? "" : value });
          }}
        />
      </div>
    );
  }
  const numeric = config?.data_type !== "categorical";
  return (
    <>
      {numeric ? (
        <div className="flex flex-col gap-2">
          <span id={`${id}-comparator`} className="text-label font-semibold text-foreground">
            Comparator
          </span>
          <ChoiceButtons
            choices={COMPARATORS}
            value={draft.comparator}
            a11y={{ labelledBy: `${id}-comparator` }}
            onChange={(comparator) => {
              onChange({ ...draft, comparator });
            }}
          />
        </div>
      ) : null}
      <FormField label="Value">
        <Input
          value={draft.value}
          inputMode={numeric ? "decimal" : "text"}
          autoComplete="off"
          placeholder={numeric ? "e.g. 0.8" : "Exact value"}
          className={numeric ? "font-mono" : undefined}
          onChange={(event) => {
            onChange({
              ...draft,
              comparator: numeric ? draft.comparator : "eq",
              value: event.target.value,
            });
          }}
        />
      </FormField>
    </>
  );
}

interface HasScoresToggleProps {
  pressed: boolean;
  onChange: (pressed: boolean) => void;
  chipClassName: string;
}

/** The "Has scores" chip: keeps only traces with any score, from any source. */
export function HasScoresToggle({ pressed, onChange, chipClassName }: HasScoresToggleProps) {
  return (
    <Button
      variant="ghost"
      aria-pressed={pressed}
      onClick={() => {
        onChange(!pressed);
      }}
      className={cn(chipClassName, pressed && "border-accent/60 bg-accent-subtle text-accent")}
    >
      Has scores
    </Button>
  );
}
