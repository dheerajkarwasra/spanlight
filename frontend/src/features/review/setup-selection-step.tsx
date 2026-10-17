import { CircleAlert, Clock, Coins, Shuffle, Stethoscope, type LucideIcon } from "lucide-react";
import { useId } from "react";

import { ChipMultiSelect } from "@/components/chip-multi-select";
import { FormField } from "@/components/form-field";
import { Notice } from "@/components/notice";
import { Switch } from "@/components/ui/switch";
import { Input } from "@/components/ui/input";

import { useEnvironmentsQuery } from "./review-queries";
import { capError, selectsNothing, type SelectionDraft } from "./setup-steps";

const INTERNAL_ENVIRONMENTS = new Set(["evals", "playground", "experiments", "doctor"]);
const MAX_ENVIRONMENTS = 20;

type Toggle = "include_doctor_findings" | "include_errors" | "include_cost_outliers";

const TOGGLES: { key: Toggle; icon: LucideIcon; title: string; text: string }[] = [
  {
    key: "include_doctor_findings",
    icon: Stethoscope,
    title: "Open Doctor findings",
    text: "Traces named as evidence in an open finding",
  },
  {
    key: "include_errors",
    icon: CircleAlert,
    title: "Errors",
    text: "Traces with at least one failed span",
  },
  {
    key: "include_cost_outliers",
    icon: Coins,
    title: "Cost outliers",
    text: "Cost above the p99 of the same trace name over 7 days",
  },
];

interface SetupSelectionStepProps {
  draft: SelectionDraft;
  onChange: (draft: SelectionDraft) => void;
  /** The queue the policy fills, for the summary line. */
  queueName: string;
}

/** Step 3, "Selection": the random share, the three reasons, the daily cap and environments. */
export function SetupSelectionStep({ draft, onChange, queueName }: SetupSelectionStepProps) {
  const titleId = useId();
  const rateId = useId();
  const environments = useEnvironmentsQuery().data ?? [];
  const cap = capError(draft.dailyCap);
  const set = (change: Partial<SelectionDraft>) => onChange({ ...draft, ...change });

  return (
    <section className="flex flex-col gap-4" aria-labelledby={titleId}>
      <h3 id={titleId} className="text-base font-bold text-foreground">
        Selection
      </h3>
      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between gap-2">
          <label htmlFor={rateId} className="flex items-center gap-2 text-sm font-semibold">
            <Shuffle aria-hidden className="size-4" />
            Random share
          </label>
          <span className="text-sm font-semibold text-accent tabular">{draft.randomPercent}%</span>
        </div>
        <input
          id={rateId}
          type="range"
          min={0}
          max={5}
          step={0.1}
          value={draft.randomPercent}
          aria-valuetext={`${String(draft.randomPercent)}%`}
          onChange={(event) => set({ randomPercent: Number(event.target.value) })}
          className="w-full accent-[var(--accent)]"
        />
        <div aria-hidden className="flex justify-between text-xs text-muted-foreground">
          <span>0%</span>
          <span>5%</span>
        </div>
        <p className="text-xs text-muted-foreground">
          Random reads find the failure kinds nobody has named yet. The last quarter of the cap is
          kept for them.
        </p>
      </div>
      <ul className="flex flex-col gap-2">
        {TOGGLES.map(({ key, icon: Icon, title, text }) => (
          <ToggleRow
            key={key}
            icon={Icon}
            title={title}
            text={text}
            checked={draft[key]}
            onChange={(on) => set({ [key]: on })}
          />
        ))}
      </ul>
      <div className="grid gap-4 sm:grid-cols-[minmax(0,180px)_minmax(0,1fr)]">
        <FormField label="Daily cap" error={cap ?? undefined} hint="1 to 500 traces a day.">
          <Input
            inputMode="numeric"
            autoComplete="off"
            value={draft.dailyCap}
            onChange={(event) => set({ dailyCap: event.target.value })}
          />
        </FormField>
        <FormField
          label="Environments"
          hint="Empty means every environment. Internal ones (evals, playground, experiments, doctor) are never selected."
        >
          <ChipMultiSelect
            value={draft.environments}
            onChange={(value) => set({ environments: value })}
            options={environments
              .filter((name) => !INTERNAL_ENVIRONMENTS.has(name))
              .map((name) => ({ value: name, label: name }))}
            placeholder="All environments"
            label="Environments"
            max={MAX_ENVIRONMENTS}
          />
        </FormField>
      </div>
      {selectsNothing(draft) ? (
        <Notice tone="warning" role="status">
          Turn on at least one reason or set a random share above 0%, or nothing is selected.
        </Notice>
      ) : (
        <Notice icon={Clock}>
          Every day at 01:00 UTC, traces that ended the day before are queued in {queueName}
          {cap === null ? `, up to ${draft.dailyCap.trim()}` : ""}.
        </Notice>
      )}
    </section>
  );
}

interface ToggleRowProps {
  icon: LucideIcon;
  title: string;
  text: string;
  checked: boolean;
  onChange: (on: boolean) => void;
}

function ToggleRow({ icon: Icon, title, text, checked, onChange }: ToggleRowProps) {
  const titleId = useId();
  const textId = useId();
  return (
    <li className="flex items-center gap-3 rounded-tile bg-surface-muted px-4 py-3">
      <Icon aria-hidden className="size-4 shrink-0 text-foreground" />
      <span className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span id={titleId} className="text-sm font-semibold text-foreground">
          {title}
        </span>
        <span id={textId} className="text-xs text-muted-foreground">
          {text}
        </span>
      </span>
      <Switch
        checked={checked}
        aria-labelledby={titleId}
        aria-describedby={textId}
        onCheckedChange={onChange}
      />
    </li>
  );
}
